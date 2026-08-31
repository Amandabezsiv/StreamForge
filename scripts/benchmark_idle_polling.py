"""Measure PostgreSQL overhead from workers polling an empty queue."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_repeated import summarize
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingJob
from streamforge.models.types import JobStatus

DEFAULT_OUTPUT = Path("experiments/013-empty-queue-idle-polling/results.json")
SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}


def parse_size(value: str) -> float:
    amount = "".join(
        character
        for character in value.strip()
        if character.isdigit() or character == "."
    )
    unit = value.strip()[len(amount) :].strip().upper()
    return float(amount) * SIZE_UNITS[unit]


def container_ids() -> tuple[str, list[str]]:
    postgres = subprocess.run(
        ["docker", "compose", "ps", "-q", "postgres"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    workers = subprocess.run(
        ["docker", "compose", "ps", "-q", "worker"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    return postgres, [worker for worker in workers if worker]


def read_resources(postgres_id: str, worker_ids: list[str]) -> dict:
    ids = [postgres_id, *worker_ids]
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
        check=True,
        text=True,
        capture_output=True,
    )
    postgres_cpu = 0.0
    postgres_memory = 0.0
    worker_cpu = 0.0
    worker_memory = 0.0
    for line in result.stdout.splitlines():
        item = json.loads(line)
        memory_used, _limit = item["MemUsage"].split("/")
        cpu = float(item["CPUPerc"].rstrip("%"))
        memory = parse_size(memory_used)
        if item["ID"].startswith(postgres_id[:12]):
            postgres_cpu = cpu
            postgres_memory = memory
        else:
            worker_cpu += cpu
            worker_memory += memory
    return {
        "postgres_cpu_percent": postgres_cpu,
        "postgres_memory_bytes": postgres_memory,
        "worker_cpu_percent_total": worker_cpu,
        "worker_memory_bytes_total": worker_memory,
    }


class ResourceSampler:
    def __init__(self, postgres_id: str, worker_ids: list[str]) -> None:
        self.postgres_id = postgres_id
        self.worker_ids = worker_ids
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(read_resources(self.postgres_id, self.worker_ids))
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                KeyError,
                json.JSONDecodeError,
            ):
                pass
            self._stop.wait(0.25)

    def summary(self) -> dict:
        def metric(name: str) -> dict:
            values = [sample[name] for sample in self.samples]
            return summarize(values) if values else {}

        return {
            "sample_count": len(self.samples),
            "postgres_cpu_percent": metric("postgres_cpu_percent"),
            "postgres_memory_mib": {
                key: value / (1024**2) if key != "count" else value
                for key, value in metric("postgres_memory_bytes").items()
            },
            "worker_cpu_percent_total": metric("worker_cpu_percent_total"),
            "worker_memory_mib_total": {
                key: value / (1024**2) if key != "count" else value
                for key, value in metric("worker_memory_bytes_total").items()
            },
        }


def database_snapshot(engine) -> dict:
    with engine.connect() as connection:
        row = connection.execute(text("""
                SELECT xact_commit, xact_rollback, tup_inserted, tup_updated,
                       tup_deleted, numbackends
                FROM pg_stat_database
                WHERE datname = current_database()
                """)).mappings().one()
    return dict(row)


def reset_query_statistics(engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
        connection.execute(text("SELECT pg_stat_statements_reset()"))


def polling_query_statistics(engine) -> dict:
    with engine.connect() as connection:
        row = connection.execute(text("""
                SELECT COALESCE(sum(calls), 0) AS calls,
                       COALESCE(sum(total_exec_time), 0) AS total_exec_time_ms
                FROM pg_stat_statements
                WHERE query ILIKE '%processing_jobs%'
                  AND query ILIKE 'SELECT%'
                  AND query NOT ILIKE '%pg_stat_statements%'
                """)).mappings().one()
    calls = int(row["calls"])
    total_exec_time_ms = float(row["total_exec_time_ms"])
    return {
        "calls": calls,
        "total_exec_time_ms": total_exec_time_ms,
        "mean_exec_time_ms": total_exec_time_ms / calls if calls else None,
    }


def configure_workers(count: int, interval: float) -> None:
    if count == 0:
        compose("stop", "worker")
        return
    environment = os.environ.copy()
    environment.update(
        {
            "WORKER_POLL_INTERVAL": str(interval),
            "JOB_NOTIFICATIONS_ENABLED": "false",
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
        f"worker={count}",
        "worker",
        env=environment,
    )


def run_configuration(engine, workers: int, interval: float, duration: float) -> dict:
    configure_workers(workers, interval)
    time.sleep(2.0)
    postgres_id, worker_ids = container_ids()
    if len(worker_ids) != workers:
        raise RuntimeError(f"Expected {workers} workers, found {len(worker_ids)}")
    reset_query_statistics(engine)
    before = database_snapshot(engine)
    sampler = ResourceSampler(postgres_id, worker_ids)
    sampler.start()
    started = time.monotonic()
    time.sleep(duration)
    measured_duration = time.monotonic() - started
    sampler.stop()
    after = database_snapshot(engine)
    query_latency = polling_query_statistics(engine)
    delta = {
        key: after[key] - before[key]
        for key in (
            "xact_commit",
            "xact_rollback",
            "tup_inserted",
            "tup_updated",
            "tup_deleted",
        )
    }
    empty_polls = max(0, delta["xact_rollback"])
    return {
        "workers": workers,
        "poll_interval_seconds": interval,
        "duration_seconds": measured_duration,
        "expected_polls_per_second": workers / interval if workers else 0.0,
        "observed_empty_polls": empty_polls,
        "observed_empty_polls_per_second": empty_polls / measured_duration,
        "database_transactions": delta,
        "database_transactions_per_second": {
            "commits": delta["xact_commit"] / measured_duration,
            "rollbacks": delta["xact_rollback"] / measured_duration,
            "total": (delta["xact_commit"] + delta["xact_rollback"])
            / measured_duration,
        },
        "database_connections": {
            "before": before["numbackends"],
            "after": after["numbackends"],
        },
        "polling_query_latency": query_latency,
        "resources": sampler.summary(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("duration must be positive")

    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        connect_args={"application_name": "streamforge-idle-polling-benchmark"},
    )
    session_factory = sessionmaker(bind=engine)
    matrix = [
        (0, 2.0),
        (1, 2.0),
        (4, 2.0),
        (8, 2.0),
        (8, 1.0),
        (8, 0.5),
        (8, 0.1),
        (4, 0.1),
        (4, 0.5),
        (4, 1.0),
        (4, 5.0),
    ]
    try:
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
        for workers, interval in matrix:
            print(
                f"Measuring {workers} workers at {interval:g}s polling",
                flush=True,
            )
            runs.append(run_configuration(engine, workers, interval, args.duration))
        baseline = runs[0]
        baseline_transactions = baseline["database_transactions_per_second"]["total"]
        for run in runs:
            run["polling_transaction_overhead_per_second"] = max(
                0.0,
                run["database_transactions_per_second"]["total"]
                - baseline_transactions,
            )
        result = {
            "experiment": "013-empty-queue-idle-polling",
            "recorded_at": datetime.now(UTC).isoformat(),
            "configuration": {
                "duration_seconds_per_configuration": args.duration,
                "queue_state": "empty",
                "matrix": [
                    {"workers": workers, "poll_interval_seconds": interval}
                    for workers, interval in matrix
                ],
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
