"""Compare LISTEN/NOTIFY job wake-ups with polling-only workers."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_polling_latency_cost import measure_idle_cost, submit_and_wait_for_claim
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingJob
from streamforge.models.types import JobStatus

DEFAULT_OUTPUT = Path("experiments/015-listen-notify-vs-polling/results.json")
DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-small.mp4")
PHASE_WINDOW_SECONDS = 2.0
MODES = (
    {
        "name": "polling-2s",
        "notifications_enabled": False,
        "poll_interval_seconds": 2.0,
    },
    {
        "name": "polling-500ms",
        "notifications_enabled": False,
        "poll_interval_seconds": 0.5,
    },
    {
        "name": "polling-100ms",
        "notifications_enabled": False,
        "poll_interval_seconds": 0.1,
    },
    {
        "name": "listen-notify-30s-fallback",
        "notifications_enabled": True,
        "poll_interval_seconds": 30.0,
    },
)


def configure_worker(mode: dict) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "WORKER_POLL_INTERVAL": str(mode["poll_interval_seconds"]),
            "JOB_NOTIFICATIONS_ENABLED": str(mode["notifications_enabled"]).lower(),
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
        "worker=1",
        "worker",
        env=environment,
    )


def measure_pickup_latency(
    api_url: str,
    fixture: Path,
    mode: dict,
    trials: int,
    timeout: float,
    randomizer: random.Random,
) -> dict:
    samples = []
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        wait_for_api(client, timeout)
        for trial in range(1, trials + 1):
            phase_delay = randomizer.uniform(0.0, PHASE_WINDOW_SECONDS)
            time.sleep(phase_delay)
            sample = submit_and_wait_for_claim(
                client,
                fixture,
                mode["poll_interval_seconds"],
                trial,
                timeout,
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
    parser.add_argument("--seed", type=int, default=1501)
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
        connect_args={"application_name": "streamforge-listen-notify-benchmark"},
    )
    session_factory = sessionmaker(bind=engine)
    randomizer = random.Random(args.seed)
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

        runs = []
        for mode in MODES:
            print(f"Measuring {mode['name']}", flush=True)
            configure_worker(mode)
            time.sleep(2.0)
            idle_cost = measure_idle_cost(engine, args.idle_duration)
            pickup = measure_pickup_latency(
                args.api_url,
                args.fixture,
                mode,
                args.trials,
                args.timeout,
                randomizer,
            )
            runs.append(
                {
                    "mode": mode,
                    "idle_database_cost": idle_cost,
                    "job_pickup_latency": pickup,
                }
            )

        result = {
            "experiment": "015-listen-notify-vs-polling",
            "recorded_at": datetime.now(UTC).isoformat(),
            "configuration": {
                "worker_count": 1,
                "modes": list(MODES),
                "pickup_trials_per_mode": args.trials,
                "idle_cost_duration_seconds": args.idle_duration,
                "submission_phase_window_seconds": PHASE_WINDOW_SECONDS,
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
        restore_normal_workers()


if __name__ == "__main__":
    main()
