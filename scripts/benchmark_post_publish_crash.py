"""Kill a worker after atomic rename but before VideoOutput commit."""

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
from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_lease_recovery import wait_for_recovery
from benchmark_worker_failure import compose, inspect_state, wait_for_api

EXPERIMENT_DIR = Path("experiments/011-post-publication-pre-commit-crash")


def wait_for_final_file(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return
        time.sleep(0.02)
    raise TimeoutError(f"Atomic output {path} was not published within {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video",
        type=Path,
        default=Path("storage/benchmark-fixtures/baseline-large.mp4"),
    )
    parser.add_argument("--lease-seconds", type=float, default=6.0)
    parser.add_argument("--renewal-seconds", type=float, default=2.0)
    parser.add_argument("--publish-delay-seconds", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"fixture does not exist: {args.video}")

    environment = os.environ.copy()
    environment.update(
        {
            "JOB_LEASE_SECONDS": str(args.lease_seconds),
            "JOB_LEASE_RENEWAL_SECONDS": str(args.renewal_seconds),
            "FFMPEG_THREADS": "3",
            "DIAGNOSTIC_PUBLISH_COMMIT_DELAY_SECONDS": str(args.publish_delay_seconds),
        }
    )
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    print("Building and starting a worker with the post-publication delay", flush=True)
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

    video_directory = Path("storage/videos") / str(video_id)
    final_path = video_directory / "720p.mp4"
    print("Waiting for atomic 720p publication", flush=True)
    wait_for_final_file(final_path, args.timeout)
    published_at = datetime.now(UTC)
    print("Final file exists; killing worker before database commit", flush=True)
    compose("kill", "-s", "SIGKILL", "worker")
    compose("stop", "worker")
    time.sleep(0.5)
    failure_snapshot = inspect_state(video_id, Path("storage"))

    recovery_environment = environment.copy()
    recovery_environment["DIAGNOSTIC_PUBLISH_COMMIT_DELAY_SECONDS"] = "0"
    print("Starting two workers and waiting for lease recovery", flush=True)
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        "worker=2",
        "worker",
        env=recovery_environment,
    )
    recovery = wait_for_recovery(video_id, args.timeout)
    temporary_files = sorted(str(path) for path in video_directory.glob(".*.tmp"))
    final_snapshot = next(
        item
        for item in failure_snapshot["filesystem"]["files"]
        if item["path"].endswith("/720p.mp4")
    )
    snapshot_output_types = [
        output["type"] for output in failure_snapshot["database"]["outputs"]
    ]
    recovery_output_types = [output["type"] for output in recovery["outputs"]]
    attempts = recovery["jobs"]
    passed = (
        final_snapshot["ffprobe_valid"] is True
        and "TRANSCODED_VIDEO" not in snapshot_output_types
        and len(attempts) == 2
        and attempts[0]["status"] == "FAILED"
        and attempts[0]["error_code"] == "WorkerLeaseExpired"
        and attempts[1]["status"] == "COMPLETED"
        and recovery["video_status"] == "READY"
        and recovery_output_types.count("THUMBNAIL") == 1
        and recovery_output_types.count("TRANSCODED_VIDEO") == 1
        and not temporary_files
    )
    result = {
        "experiment": "011-post-publication-pre-commit-crash",
        "recorded_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "lease_seconds": args.lease_seconds,
            "renewal_seconds": args.renewal_seconds,
            "publish_commit_delay_seconds": args.publish_delay_seconds,
        },
        "published_at": published_at.isoformat(),
        "failure_snapshot": failure_snapshot,
        "recovery": recovery,
        "temporary_files_after_recovery": temporary_files,
        "passed": passed,
    }
    (EXPERIMENT_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    restore_normal_workers()
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
