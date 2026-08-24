"""Run one reproducible upload-to-READY StreamForge benchmark."""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-10s.mp4")
DEFAULT_RESULT = Path("experiments/001-single-worker-baseline/results.json")


def generate_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    container_path = f"/app/storage/{path.relative_to('storage').as_posix()}"
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "worker",
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            "10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            container_path,
        ],
        check=True,
    )


def wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError("StreamForge API did not become healthy")


def run_benchmark(api_url: str, video_path: Path, timeout: float) -> dict:
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        wait_for_api(client, timeout)

        upload_started = time.perf_counter()
        with video_path.open("rb") as video_file:
            response = client.post(
                "/api/v1/videos",
                files={"file": (video_path.name, video_file, "video/mp4")},
            )
        upload_duration = time.perf_counter() - upload_started
        response.raise_for_status()
        video_id = response.json()["video_id"]

        deadline = time.monotonic() + timeout
        video = None
        jobs = []
        while time.monotonic() < deadline:
            video_response = client.get(f"/api/v1/videos/{video_id}")
            video_response.raise_for_status()
            video = video_response.json()
            jobs_response = client.get(f"/api/v1/videos/{video_id}/jobs")
            jobs_response.raise_for_status()
            jobs = jobs_response.json()
            if video["status"] in {"READY", "FAILED"}:
                break
            time.sleep(0.25)
        else:
            raise TimeoutError(f"Video {video_id} did not finish within {timeout}s")

        outputs_response = client.get(f"/api/v1/videos/{video_id}/outputs")
        outputs_response.raise_for_status()
        outputs = outputs_response.json()
        job = jobs[-1]
        output_types = {output["type"] for output in outputs}
        metadata_complete = all(
            video[field] is not None
            for field in (
                "duration_seconds",
                "width",
                "height",
                "codec",
                "bitrate",
                "fps",
            )
        )

        errors = []
        if job["error_code"]:
            errors.append(
                {
                    "code": job["error_code"],
                    "message": job["error_message"],
                }
            )

        return {
            "benchmark": "001-single-worker-baseline",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "video_id": video_id,
            "fixture": {
                "filename": video_path.name,
                "size_bytes": video_path.stat().st_size,
            },
            "result": {
                "video_status": video["status"],
                "job_status": job["status"],
                "metadata_complete": metadata_complete,
                "thumbnail_registered": "THUMBNAIL" in output_types,
                "transcoded_720p_registered": "TRANSCODED_VIDEO" in output_types,
            },
            "metrics_seconds": {
                "upload_duration": upload_duration,
                "queue_wait_time": job["queue_wait_seconds"],
                "processing_duration": job["processing_duration_seconds"],
                "metadata_duration": job["metadata_duration_seconds"],
                "thumbnail_duration": job["thumbnail_duration_seconds"],
                "transcoding_duration": job["transcoding_duration_seconds"],
                "total_time_to_ready": job["total_time_to_ready_seconds"],
            },
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--video", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--regenerate-fixture", action="store_true")
    args = parser.parse_args()

    if args.regenerate_fixture or not args.video.exists():
        generate_fixture(args.video)

    result = run_benchmark(args.api_url, args.video, args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

    checks = result["result"]
    if not all(checks.values()) or result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
