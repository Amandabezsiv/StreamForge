"""Submit videos concurrently and measure queue growth with one worker."""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES

DEFAULT_OUTPUT = Path("experiments/002-concurrent-medium-queue/results.json")


def upload_one(
    api_url: str,
    video_path: Path,
    barrier: threading.Barrier,
    request_number: int,
) -> dict:
    with httpx.Client(base_url=api_url, timeout=120.0) as client:
        barrier.wait()
        started = time.perf_counter()
        with video_path.open("rb") as video_file:
            response = client.post(
                "/api/v1/videos",
                files={
                    "file": (
                        f"concurrent-medium-{request_number:02d}.mp4",
                        video_file,
                        "video/mp4",
                    )
                },
            )
        duration = time.perf_counter() - started
        response.raise_for_status()
        return {
            "request_number": request_number,
            "video_id": response.json()["video_id"],
            "upload_duration_seconds": duration,
        }


def run_experiment(
    api_url: str, video_path: Path, concurrency: int, timeout: float
) -> dict:
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        wait_for_api(client, timeout)

    barrier = threading.Barrier(concurrency)
    batch_started = time.perf_counter()
    uploads = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(upload_one, api_url, video_path, barrier, number)
            for number in range(1, concurrency + 1)
        ]
        for future in as_completed(futures):
            uploads.append(future.result())
    all_uploads_accepted_seconds = time.perf_counter() - batch_started

    deadline = time.monotonic() + timeout
    completed = {}
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        while time.monotonic() < deadline and len(completed) < concurrency:
            for upload in uploads:
                video_id = upload["video_id"]
                if video_id in completed:
                    continue
                video_response = client.get(f"/api/v1/videos/{video_id}")
                video_response.raise_for_status()
                video = video_response.json()
                if video["status"] not in {"READY", "FAILED"}:
                    continue
                jobs_response = client.get(f"/api/v1/videos/{video_id}/jobs")
                jobs_response.raise_for_status()
                outputs_response = client.get(f"/api/v1/videos/{video_id}/outputs")
                outputs_response.raise_for_status()
                completed[video_id] = {
                    "video": video,
                    "job": jobs_response.json()[-1],
                    "outputs": outputs_response.json(),
                }
            if len(completed) < concurrency:
                time.sleep(0.25)

    if len(completed) != concurrency:
        pending = concurrency - len(completed)
        raise TimeoutError(f"{pending} videos did not finish within {timeout}s")

    batch_time_to_ready = time.perf_counter() - batch_started
    videos = []
    for upload in uploads:
        item = completed[upload["video_id"]]
        job = item["job"]
        output_types = {output["type"] for output in item["outputs"]}
        videos.append(
            {
                **upload,
                "video_status": item["video"]["status"],
                "job_status": job["status"],
                "queue_wait_time": job["queue_wait_seconds"],
                "processing_duration": job["processing_duration_seconds"],
                "transcoding_duration": job["transcoding_duration_seconds"],
                "total_time_to_ready": job["total_time_to_ready_seconds"],
                "thumbnail_registered": "THUMBNAIL" in output_types,
                "transcoded_720p_registered": "TRANSCODED_VIDEO" in output_types,
                "error_code": job["error_code"],
                "error_message": job["error_message"],
            }
        )

    videos.sort(key=lambda item: item["queue_wait_time"])
    for queue_position, video in enumerate(videos, start=1):
        video["queue_position"] = queue_position

    metric_names = (
        "queue_wait_time",
        "processing_duration",
        "transcoding_duration",
        "total_time_to_ready",
    )
    return {
        "benchmark": "002-concurrent-medium-queue",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "worker_count": 1,
            "concurrent_uploads": concurrency,
            "fixture_filename": video_path.name,
            "fixture_size_bytes": video_path.stat().st_size,
            "fixture_duration_seconds": FIXTURES["medium"]["duration_seconds"],
            "fixture_resolution": FIXTURES["medium"]["resolution"],
        },
        "batch": {
            "all_uploads_accepted_seconds": all_uploads_accepted_seconds,
            "all_videos_ready_seconds": batch_time_to_ready,
            "completed": sum(video["video_status"] == "READY" for video in videos),
            "failed": sum(video["video_status"] == "FAILED" for video in videos),
        },
        "summary_seconds": {
            metric: summarize([video[metric] for video in videos])
            for metric in metric_names
        },
        "videos_by_queue_position": videos,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--regenerate-fixture", action="store_true")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    profile = FIXTURES["medium"]
    video_path = Path("storage/benchmark-fixtures") / profile["filename"]
    if args.regenerate_fixture or not video_path.exists():
        generate_fixture(
            video_path,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    result = run_experiment(
        args.api_url, video_path, args.concurrency, args.timeout
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

    videos = result["videos_by_queue_position"]
    successful = all(
        video["video_status"] == "READY"
        and video["job_status"] == "COMPLETED"
        and video["thumbnail_registered"]
        and video["transcoded_720p_registered"]
        and video["error_code"] is None
        for video in videos
    )
    if not successful:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
