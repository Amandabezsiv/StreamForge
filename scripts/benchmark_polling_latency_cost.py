"""Compare real job pickup latency with empty-poll PostgreSQL cost."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_idle_polling import (
    ResourceSampler,
    configure_workers,
    container_ids,
    database_snapshot,
    polling_query_statistics,
    reset_query_statistics,
)
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingJob
from streamforge.models.types import JobStatus

DEFAULT_OUTPUT = Path("experiments/014-polling-latency-vs-database-cost/results.json")
DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-small.mp4")
INTERVALS = (2.0, 1.0, 0.5, 0.1)


def wait_for_database(engine, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OSError:
            time.sleep(0.5)
        except OperationalError:
            time.sleep(0.5)
    raise TimeoutError("PostgreSQL did not become ready")


def measure_idle_cost(engine, duration: float) -> dict:
    postgres_id, worker_ids = container_ids()
    reset_query_statistics(engine)
    before = database_snapshot(engine)
    sampler = ResourceSampler(postgres_id, worker_ids)
    sampler.start()
    started = time.monotonic()
    time.sleep(duration)
    measured_duration = time.monotonic() - started
    sampler.stop()
    after = database_snapshot(engine)
    latency = polling_query_statistics(engine)
    commits = after["xact_commit"] - before["xact_commit"]
    rollbacks = after["xact_rollback"] - before["xact_rollback"]
    return {
        "duration_seconds": measured_duration,
        "empty_polls": rollbacks,
        "empty_polls_per_second": rollbacks / measured_duration,
        "database_transactions": {
            "commits": commits,
            "rollbacks": rollbacks,
            "total": commits + rollbacks,
        },
        "database_transactions_per_second": (commits + rollbacks) / measured_duration,
        "polling_query_latency": latency,
        "database_connections": {
            "before": before["numbackends"],
            "after": after["numbackends"],
        },
        "resources": sampler.summary(),
    }


def submit_and_wait_for_claim(
    client: httpx.Client,
    fixture: Path,
    interval: float,
    trial: int,
    timeout: float,
) -> dict:
    upload_started = time.perf_counter()
    with fixture.open("rb") as video_file:
        response = client.post(
            "/api/v1/videos",
            files={
                "file": (
                    f"poll-{interval:g}s-{trial:02d}.mp4",
                    video_file,
                    "video/mp4",
                )
            },
        )
    upload_duration = time.perf_counter() - upload_started
    response.raise_for_status()
    video_id = response.json()["video_id"]
    deadline = time.monotonic() + timeout
    claimed_job = None
    video_status = None
    while time.monotonic() < deadline:
        jobs_response = client.get(f"/api/v1/videos/{video_id}/jobs")
        jobs_response.raise_for_status()
        jobs = jobs_response.json()
        if jobs and jobs[-1]["queue_wait_seconds"] is not None:
            claimed_job = jobs[-1]
        video_response = client.get(f"/api/v1/videos/{video_id}")
        video_response.raise_for_status()
        video_status = video_response.json()["status"]
        job_is_terminal = claimed_job is not None and claimed_job["status"] in {
            "COMPLETED",
            "FAILED",
        }
        if job_is_terminal and video_status in {"READY", "FAILED"}:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"Video {video_id} did not complete within {timeout}s")
    if video_status != "READY" or claimed_job["status"] != "COMPLETED":
        raise RuntimeError(
            f"Trial {trial} ended as video={video_status}, "
            f"job={claimed_job['status']}"
        )
    return {
        "trial": trial,
        "video_id": video_id,
        "upload_duration_seconds": upload_duration,
        "queue_wait_seconds": claimed_job["queue_wait_seconds"],
    }


def measure_pickup_latency(
    api_url: str,
    fixture: Path,
    interval: float,
    trials: int,
    timeout: float,
    randomizer: random.Random,
) -> dict:
    samples = []
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        wait_for_api(client, timeout)
        for trial in range(1, trials + 1):
            phase_delay = randomizer.uniform(0.0, interval)
            time.sleep(phase_delay)
            sample = submit_and_wait_for_claim(
                client, fixture, interval, trial, timeout
            )
            sample["pre_submission_delay_seconds"] = phase_delay
            samples.append(sample)
            print(
                f"  trial {trial}/{trials}: "
                f"pickup={sample['queue_wait_seconds']:.3f}s",
                flush=True,
            )
    return {
        "trials": trials,
        "queue_wait_seconds": summarize(
            [sample["queue_wait_seconds"] for sample in samples]
        ),
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--idle-duration", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=1401)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.trials < 1 or args.idle_duration <= 0:
        parser.error("trials and idle duration must be positive")

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
        connect_args={"application_name": "streamforge-poll-latency-cost"},
    )
    session_factory = sessionmaker(bind=engine)
    randomizer = random.Random(args.seed)
    try:
        compose("up", "-d", "postgres", "api")
        wait_for_database(engine, args.timeout)
        compose("stop", "worker")
        with session_factory() as db:
            pending = db.scalar(
                select(ProcessingJob.id)
                .where(ProcessingJob.status == JobStatus.PENDING)
                .limit(1)
            )
        if pending is not None:
            raise RuntimeError("Refusing to run with unrelated PENDING jobs")

        runs = []
        for interval in INTERVALS:
            print(f"Measuring one worker at {interval:g}s polling", flush=True)
            configure_workers(1, interval)
            time.sleep(2.0)
            idle_cost = measure_idle_cost(engine, args.idle_duration)
            pickup = measure_pickup_latency(
                args.api_url,
                args.fixture,
                interval,
                args.trials,
                args.timeout,
                randomizer,
            )
            runs.append(
                {
                    "poll_interval_seconds": interval,
                    "idle_database_cost": idle_cost,
                    "job_pickup_latency": pickup,
                }
            )

        result = {
            "experiment": "014-polling-latency-vs-database-cost",
            "recorded_at": datetime.now(UTC).isoformat(),
            "configuration": {
                "worker_count": 1,
                "poll_intervals_seconds": list(INTERVALS),
                "pickup_trials_per_interval": args.trials,
                "idle_cost_duration_seconds": args.idle_duration,
                "random_seed": args.seed,
                "fixture": str(args.fixture),
                "fixture_size_bytes": args.fixture.stat().st_size,
            },
            "runs": runs,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        engine.dispose()
        compose("up", "-d", "postgres", "api")
        restore_normal_workers()


if __name__ == "__main__":
    main()
