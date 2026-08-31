"""Verify polling recovery after a PostgreSQL notification is missed."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_listen_notify import configure_worker
from benchmark_polling_latency_cost import submit_and_wait_for_claim
from benchmark_sizes import FIXTURES
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingEvent, ProcessingJob
from streamforge.models.types import JobStatus
from streamforge.workers.notifications import LISTENER_APPLICATION_NAME

DEFAULT_OUTPUT = Path("experiments/016-listen-notify-failure-recovery/results.json")
DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-small.mp4")


def listener_pids(engine) -> list[int]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                text("""
                    SELECT pid
                    FROM pg_stat_activity
                    WHERE application_name = :application_name
                    ORDER BY pid
                    """),
                {"application_name": LISTENER_APPLICATION_NAME},
            )
        )


def wait_for_listener(engine, timeout: float, excluded_pid: int | None = None) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pids = listener_pids(engine)
        candidates = [pid for pid in pids if pid != excluded_pid]
        if len(candidates) == 1:
            return candidates[0]
        time.sleep(0.1)
    raise TimeoutError("Expected one PostgreSQL job listener")


def wait_for_no_listeners(engine, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listener_pids(engine):
            return
        time.sleep(0.01)
    raise TimeoutError("PostgreSQL listener backend did not disconnect")


def terminate_listener(engine, pid: int) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--fallback-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.fallback_interval <= 0:
        parser.error("fallback interval must be positive")

    if not args.fixture.exists():
        profile = FIXTURES["small"]
        generate_fixture(
            args.fixture,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        connect_args={"application_name": "streamforge-notify-failure-benchmark"},
    )
    session_factory = sessionmaker(bind=engine)
    mode = {
        "name": "listen-notify-failure-recovery",
        "notifications_enabled": True,
        "poll_interval_seconds": args.fallback_interval,
    }
    try:
        compose("up", "-d", "postgres", "api")
        compose("stop", "worker")
        with session_factory() as db:
            pending = db.scalar(
                select(ProcessingJob.id)
                .where(ProcessingJob.status == JobStatus.PENDING)
                .limit(1)
            )
        if pending is not None:
            raise RuntimeError("Refusing to run with unrelated PENDING jobs")

        configure_worker(mode)
        with httpx.Client(base_url=args.api_url, timeout=30.0) as client:
            wait_for_api(client, args.timeout)
        original_pid = wait_for_listener(engine, args.timeout)
        if not terminate_listener(engine, original_pid):
            raise RuntimeError(f"Could not terminate listener backend {original_pid}")
        wait_for_no_listeners(engine, min(args.fallback_interval, 2.0))
        listeners_after_termination = listener_pids(engine)
        if listeners_after_termination:
            raise RuntimeError("Listener reconnected before the job was submitted")

        failure_started = time.perf_counter()
        with httpx.Client(base_url=args.api_url, timeout=30.0) as client:
            sample = submit_and_wait_for_claim(
                client,
                args.fixture,
                args.fallback_interval,
                1,
                args.timeout,
            )
            jobs_response = client.get(f"/api/v1/videos/{sample['video_id']}/jobs")
            jobs_response.raise_for_status()
            jobs = jobs_response.json()
            outputs_response = client.get(
                f"/api/v1/videos/{sample['video_id']}/outputs"
            )
            outputs_response.raise_for_status()
            outputs = outputs_response.json()
        recovery_wall_time = time.perf_counter() - failure_started
        replacement_pid = wait_for_listener(
            engine, args.timeout, excluded_pid=original_pid
        )

        with session_factory() as db:
            started_events = db.scalar(
                select(text("count(*)"))
                .select_from(ProcessingEvent)
                .where(
                    ProcessingEvent.video_id == sample["video_id"],
                    ProcessingEvent.event_type == "JOB_STARTED",
                )
            )
            completed_events = db.scalar(
                select(text("count(*)"))
                .select_from(ProcessingEvent)
                .where(
                    ProcessingEvent.video_id == sample["video_id"],
                    ProcessingEvent.event_type == "JOB_COMPLETED",
                )
            )

        result = {
            "experiment": "016-listen-notify-failure-recovery",
            "recorded_at": datetime.now(UTC).isoformat(),
            "configuration": {
                "worker_count": 1,
                "fallback_interval_seconds": args.fallback_interval,
                "fixture": str(args.fixture),
                "fixture_size_bytes": args.fixture.stat().st_size,
            },
            "failure": {
                "terminated_listener_pid": original_pid,
                "listeners_present_when_job_created": len(listeners_after_termination),
                "replacement_listener_pid": replacement_pid,
            },
            "recovery": {
                "video_id": sample["video_id"],
                "queue_wait_seconds": sample["queue_wait_seconds"],
                "wall_time_until_ready_seconds": recovery_wall_time,
                "job_count": len(jobs),
                "job_statuses": [job["status"] for job in jobs],
                "job_attempts": [job["attempt"] for job in jobs],
                "output_types": sorted(output["type"] for output in outputs),
                "job_started_events": started_events,
                "job_completed_events": completed_events,
                "duplicate_processing_detected": started_events != 1,
            },
            "checks": {
                "notification_was_missed": not listeners_after_termination,
                "claimed_within_fallback_bound": sample["queue_wait_seconds"]
                <= args.fallback_interval + 1.0,
                "completed_once": len(jobs) == 1
                and jobs[0]["status"] == "COMPLETED"
                and started_events == 1
                and completed_events == 1,
                "listener_reconnected": replacement_pid != original_pid,
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        if not all(result["checks"].values()):
            raise SystemExit(1)
    finally:
        engine.dispose()
        restore_normal_workers()


if __name__ == "__main__":
    main()
