"""Crash a leased worker and verify automatic recovery through a new attempt."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from streamforge.core.database import SessionLocal
from streamforge.models import ProcessingEvent, ProcessingJob, Video, VideoOutput
from streamforge.models.types import JobStatus, VideoStatus

EXPERIMENT_DIR = Path("experiments/009-worker-lease-recovery")


def run(command: list[str], env: dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, env=env)


def wait_for_recovery(video_id: uuid.UUID, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            jobs = list(
                db.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.video_id == video_id)
                    .order_by(ProcessingJob.attempt)
                )
            )
            if (
                video is not None
                and video.status in {VideoStatus.READY, VideoStatus.FAILED}
                and len(jobs) >= 2
                and jobs[-1].status in {JobStatus.COMPLETED, JobStatus.FAILED}
            ):
                events = list(
                    db.scalars(
                        select(ProcessingEvent)
                        .where(ProcessingEvent.video_id == video_id)
                        .order_by(ProcessingEvent.created_at)
                    )
                )
                outputs = list(
                    db.scalars(
                        select(VideoOutput).where(VideoOutput.video_id == video_id)
                    )
                )
                return {
                    "video_status": video.status,
                    "jobs": [
                        {
                            "id": str(job.id),
                            "attempt": job.attempt,
                            "status": job.status,
                            "error_code": job.error_code,
                            "claimed_by": job.claimed_by,
                            "lease_expires_at": (
                                job.lease_expires_at.isoformat()
                                if job.lease_expires_at
                                else None
                            ),
                        }
                        for job in jobs
                    ],
                    "events": [event.event_type for event in events],
                    "outputs": [
                        {
                            "type": output.type,
                            "storage_key": output.storage_key,
                            "size_bytes": output.size_bytes,
                        }
                        for output in outputs
                    ],
                }
        time.sleep(0.25)
    raise TimeoutError(f"Video {video_id} was not recovered within {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--lease-seconds", type=float, default=6.0)
    parser.add_argument("--renewal-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.renewal_seconds >= args.lease_seconds:
        parser.error("renewal interval must be shorter than the lease")

    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = EXPERIMENT_DIR / "failure-snapshot.json"
    output_path = EXPERIMENT_DIR / "results.json"
    environment = os.environ.copy()
    environment.update(
        {
            "JOB_LEASE_SECONDS": str(args.lease_seconds),
            "JOB_LEASE_RENEWAL_SECONDS": str(args.renewal_seconds),
            "FFMPEG_THREADS": "3",
        }
    )

    print("Building API and worker images with the lease migration", flush=True)
    run(["docker", "compose", "build", "api", "worker"], environment)
    print("Injecting a worker crash during transcoding", flush=True)
    run(
        [
            "uv",
            "run",
            "python",
            "scripts/benchmark_worker_failure.py",
            "--experiment-name",
            "009-worker-lease-recovery-failure",
            "--output",
            str(snapshot_path),
            "--restore-workers",
            "0",
        ],
        environment,
    )
    snapshot = json.loads(snapshot_path.read_text())
    video_id = uuid.UUID(snapshot["database"]["video_id"])

    print("Starting two workers and waiting for the lease to expire", flush=True)
    run(
        ["docker", "compose", "up", "-d", "--scale", "worker=2", "worker"],
        environment,
    )
    recovery = wait_for_recovery(video_id, args.timeout)
    video_directory = Path(snapshot["filesystem"]["directory"])
    temporary_files = sorted(str(path) for path in video_directory.glob(".*.tmp"))
    output_types = [output["type"] for output in recovery["outputs"]]
    attempts = recovery["jobs"]
    passed = (
        len(attempts) == 2
        and attempts[0]["status"] == JobStatus.FAILED
        and attempts[0]["error_code"] == "WorkerLeaseExpired"
        and attempts[1]["status"] == JobStatus.COMPLETED
        and recovery["video_status"] == VideoStatus.READY
        and output_types.count("THUMBNAIL") == 1
        and output_types.count("TRANSCODED_VIDEO") == 1
        and not temporary_files
        and "JOB_ABANDONED" in recovery["events"]
    )
    result = {
        "experiment": "009-worker-lease-recovery",
        "recorded_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "lease_seconds": args.lease_seconds,
            "renewal_seconds": args.renewal_seconds,
            "recovery_workers": 2,
        },
        "failure_snapshot": snapshot,
        "recovery": recovery,
        "temporary_files_after_recovery": temporary_files,
        "passed": passed,
    }
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print("Restoring four workers with the normal 30-second lease", flush=True)
    normal_environment = os.environ.copy()
    normal_environment.update(
        {
            "JOB_LEASE_SECONDS": "30",
            "JOB_LEASE_RENEWAL_SECONDS": "10",
            "FFMPEG_THREADS": "3",
        }
    )
    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--scale",
            "worker=4",
            "worker",
        ],
        normal_environment,
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
