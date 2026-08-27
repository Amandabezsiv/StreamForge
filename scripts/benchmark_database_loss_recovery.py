"""Pause PostgreSQL during lease renewal and verify abandoned-job recovery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_lease_recovery import wait_for_recovery
from benchmark_worker_failure import compose, wait_for_api, wait_for_event

EXPERIMENT_DIR = Path("experiments/010-database-loss-during-renewal")


def wait_for_postgres_health(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "streamforge-postgres-1",
                "--format",
                "{{.State.Health.Status}}",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(0.5)
    raise TimeoutError("PostgreSQL did not become healthy after the outage")


def restore_normal_workers() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "JOB_LEASE_SECONDS": "30",
            "JOB_LEASE_RENEWAL_SECONDS": "10",
            "FFMPEG_THREADS": "3",
            "DIAGNOSTIC_PUBLISH_COMMIT_DELAY_SECONDS": "0",
        }
    )
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        "worker=4",
        "worker",
        env=environment,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video",
        type=Path,
        default=Path("storage/benchmark-fixtures/baseline-large.mp4"),
    )
    parser.add_argument("--lease-seconds", type=float, default=6.0)
    parser.add_argument("--renewal-seconds", type=float, default=2.0)
    parser.add_argument("--database-outage-seconds", type=float, default=8.0)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"fixture does not exist: {args.video}")
    if args.database_outage_seconds <= args.lease_seconds:
        parser.error("database outage must be longer than the lease")

    environment = os.environ.copy()
    environment.update(
        {
            "JOB_LEASE_SECONDS": str(args.lease_seconds),
            "JOB_LEASE_RENEWAL_SECONDS": str(args.renewal_seconds),
            "FFMPEG_THREADS": "3",
            "DIAGNOSTIC_PUBLISH_COMMIT_DELAY_SECONDS": "0",
        }
    )
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    print("Building and starting an isolated leased worker", flush=True)
    subprocess.run(
        ["docker", "compose", "build", "api", "worker"],
        check=True,
        env=environment,
    )
    compose("down", "--remove-orphans")
    compose("up", "-d", "postgres", "api", env=environment)
    with httpx.Client(base_url="http://localhost:8000", timeout=10.0) as client:
        wait_for_api(client, args.timeout)
        compose("up", "-d", "--scale", "worker=1", "worker", env=environment)
        with args.video.open("rb") as fixture:
            response = client.post(
                "/api/v1/videos",
                files={"file": (args.video.name, fixture, "video/mp4")},
            )
        response.raise_for_status()
        video_id = uuid.UUID(response.json()["video_id"])

    wait_for_event(video_id, "TRANSCODING_STARTED", args.timeout)
    outage_started = datetime.now(UTC)
    print(
        f"Pausing PostgreSQL for {args.database_outage_seconds:.1f} seconds",
        flush=True,
    )
    compose("pause", "postgres")
    try:
        time.sleep(args.database_outage_seconds)
    finally:
        compose("unpause", "postgres")
    outage_ended = datetime.now(UTC)

    # The original owner is already stale. Stop it before it can recover and
    # immediately claim the retry that another worker is meant to demonstrate.
    compose("kill", "-s", "SIGKILL", "worker")
    compose("stop", "worker")
    wait_for_postgres_health()
    print("Starting a second worker and waiting for recovery", flush=True)
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        "worker=2",
        "worker",
        env=environment,
    )
    recovery = wait_for_recovery(video_id, args.timeout)
    video_directory = Path("storage/videos") / str(video_id)
    temporary_files = sorted(str(path) for path in video_directory.glob(".*.tmp"))
    worker_logs = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "worker"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    attempts = recovery["jobs"]
    output_types = [output["type"] for output in recovery["outputs"]]
    passed = (
        len(attempts) == 2
        and attempts[0]["status"] == "FAILED"
        and attempts[0]["error_code"] == "WorkerLeaseExpired"
        and attempts[1]["status"] == "COMPLETED"
        and recovery["video_status"] == "READY"
        and output_types.count("THUMBNAIL") == 1
        and output_types.count("TRANSCODED_VIDEO") == 1
        and not temporary_files
        and "JOB_ABANDONED" in recovery["events"]
    )
    result = {
        "experiment": "010-database-loss-during-lease-renewal",
        "recorded_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "lease_seconds": args.lease_seconds,
            "renewal_seconds": args.renewal_seconds,
            "database_outage_seconds": args.database_outage_seconds,
        },
        "database_outage": {
            "started_at": outage_started.isoformat(),
            "ended_at": outage_ended.isoformat(),
        },
        "recovery": recovery,
        "temporary_files_after_recovery": temporary_files,
        "lease_renewal_failure_logged": "job lease renewal failed" in worker_logs,
        "passed": passed,
    }
    (EXPERIMENT_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    restore_normal_workers()
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
