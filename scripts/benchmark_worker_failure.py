"""Kill a worker during transcoding and inspect durable database/filesystem state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from streamforge.core.database import SessionLocal
from streamforge.models import ProcessingEvent, ProcessingJob, Video, VideoOutput
from streamforge.models.types import JobStatus

DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-large.mp4")
DEFAULT_OUTPUT = Path("experiments/007-worker-failure/results.json")


def compose(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *arguments],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("API did not become healthy")


def wait_for_event(video_id: uuid.UUID, event_type: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            event = db.scalar(
                select(ProcessingEvent).where(
                    ProcessingEvent.video_id == video_id,
                    ProcessingEvent.event_type == event_type,
                )
            )
            if event is not None:
                return
        time.sleep(0.02)
    raise TimeoutError(f"Event {event_type} was not observed within {timeout}s")


def inspect_file(path: Path) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "ffprobe_valid": None,
        "ffprobe_error": None,
    }
    if not path.exists():
        return result
    probe_arguments = ["-v", "error", "-show_format", "-of", "json"]
    if shutil.which("ffprobe"):
        probe_command = ["ffprobe", *probe_arguments, str(path)]
    else:
        container_path = f"/app/storage/{path.relative_to('storage').as_posix()}"
        probe_command = [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "worker",
            "ffprobe",
            *probe_arguments,
            container_path,
        ]
    probe = subprocess.run(probe_command, text=True, capture_output=True, check=False)
    result["ffprobe_valid"] = probe.returncode == 0
    if probe.returncode != 0:
        result["ffprobe_error"] = probe.stderr.strip()[:1000]
    return result


def inspect_state(video_id: uuid.UUID, storage_root: Path) -> dict:
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if video is None:
            raise RuntimeError(f"Video {video_id} disappeared")
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id))
        if job is None:
            raise RuntimeError(f"Job for video {video_id} disappeared")
        events = list(
            db.scalars(
                select(ProcessingEvent)
                .where(ProcessingEvent.video_id == video_id)
                .order_by(ProcessingEvent.created_at)
            )
        )
        outputs = list(
            db.scalars(
                select(VideoOutput)
                .where(VideoOutput.video_id == video_id)
                .order_by(VideoOutput.created_at)
            )
        )
        directory = storage_root / Path(video.storage_key).parent
        return {
            "database": {
                "video_id": str(video.id),
                "video_status": video.status,
                "metadata": {
                    "duration_seconds": video.duration_seconds,
                    "width": video.width,
                    "height": video.height,
                    "codec": video.codec,
                    "bitrate": video.bitrate,
                    "fps": video.fps,
                },
                "job_id": str(job.id),
                "job_status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "events": [event.event_type for event in events],
                "outputs": [
                    {
                        "type": output.type,
                        "resolution": output.resolution,
                        "storage_key": output.storage_key,
                        "size_bytes": output.size_bytes,
                    }
                    for output in outputs
                ],
            },
            "filesystem": {
                "directory": str(directory),
                "files": [inspect_file(path) for path in sorted(directory.iterdir())],
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--video", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--restore-workers", type=int, default=4)
    parser.add_argument("--ffmpeg-threads", default="3")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"fixture does not exist: {args.video}")

    with SessionLocal() as db:
        pending = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.status == JobStatus.PENDING)
            .limit(1)
        )
    if pending is not None:
        raise RuntimeError("Refusing to run while an unrelated PENDING job exists")

    worker_env = os.environ.copy()
    worker_env["FFMPEG_THREADS"] = args.ffmpeg_threads
    video_id: uuid.UUID | None = None
    compose("stop", "worker")
    try:
        compose("up", "-d", "--scale", "worker=1", "worker", env=worker_env)
        with httpx.Client(base_url=args.api_url, timeout=120.0) as client:
            wait_for_api(client, args.timeout)
            with args.video.open("rb") as fixture:
                response = client.post(
                    "/api/v1/videos",
                    files={"file": (args.video.name, fixture, "video/mp4")},
                )
            response.raise_for_status()
            video_id = uuid.UUID(response.json()["video_id"])

        wait_for_event(video_id, "TRANSCODING_STARTED", args.timeout)
        observed_at = datetime.now(UTC)
        compose("kill", "-s", "SIGKILL", "worker")
        compose("stop", "worker")
        time.sleep(0.5)
        state = inspect_state(video_id, Path("storage"))
        result = {
            "experiment": "007-worker-failure-during-processing",
            "recorded_at": datetime.now(UTC).isoformat(),
            "failure_injection": {
                "signal": "SIGKILL",
                "trigger_event": "TRANSCODING_STARTED",
                "trigger_observed_at": observed_at.isoformat(),
                "fixture": str(args.video),
                "fixture_size_bytes": args.video.stat().st_size,
                "worker_count": 1,
                "ffmpeg_threads": args.ffmpeg_threads,
            },
            **state,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        if args.restore_workers > 0:
            compose(
                "up",
                "-d",
                "--scale",
                f"worker={args.restore_workers}",
                "worker",
                env=worker_env,
            )


if __name__ == "__main__":
    main()
