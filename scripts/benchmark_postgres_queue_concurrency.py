"""Measure PostgreSQL queue acquisition while increasing claimant concurrency."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from benchmark_database_loss_recovery import restore_normal_workers
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingEvent, ProcessingJob, Video
from streamforge.models.types import JobStatus, JobType, VideoStatus
from streamforge.workers.processor import acquire_pending_job

DEFAULT_OUTPUT = Path("experiments/012-postgresql-queue-concurrency/results.json")


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def summarize(values: list[float]) -> dict:
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def seed_jobs(session_factory: sessionmaker, count: int) -> list[uuid.UUID]:
    experiment_id = uuid.uuid4()
    video_ids = [uuid.uuid4() for _ in range(count)]
    job_ids = [uuid.uuid4() for _ in range(count)]
    with session_factory() as db:
        db.execute(
            insert(Video),
            [
                {
                    "id": video_id,
                    "original_filename": f"queue-{experiment_id}-{index}.mp4",
                    "storage_key": f"experiments/012/{experiment_id}/{index}.mp4",
                    "size_bytes": 0,
                    "status": VideoStatus.UPLOADED,
                }
                for index, video_id in enumerate(video_ids)
            ],
        )
        db.execute(
            insert(ProcessingJob),
            [
                {
                    "id": job_id,
                    "video_id": video_id,
                    "type": JobType.PROCESS_VIDEO,
                    "status": JobStatus.PENDING,
                    "attempt": 1,
                }
                for job_id, video_id in zip(job_ids, video_ids, strict=True)
            ],
        )
        db.commit()
    return video_ids


def cleanup_jobs(session_factory: sessionmaker, video_ids: list[uuid.UUID]) -> None:
    with session_factory() as db:
        db.execute(delete(Video).where(Video.id.in_(video_ids)))
        db.commit()


def database_counters(engine) -> dict:
    with engine.connect() as connection:
        row = connection.execute(text("""
                SELECT xact_commit, blks_read, blks_hit,
                       tup_inserted, tup_updated, tup_deleted
                FROM pg_stat_database
                WHERE datname = current_database()
                """)).mappings().one()
    return dict(row)


class ConnectionMonitor:
    def __init__(self, engine, interval: float = 0.02) -> None:
        self.engine = engine
        self.interval = interval
        self.samples: list[int] = []
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
                with self.engine.connect() as connection:
                    count = connection.scalar(text("""
                            SELECT count(*)
                            FROM pg_stat_activity
                            WHERE datname = current_database()
                              AND application_name = 'streamforge-queue-benchmark'
                            """))
                self.samples.append(int(count or 0))
            except SQLAlchemyError:
                pass
            self._stop.wait(self.interval)


def claim_until_empty(
    session_factory: sessionmaker,
    barrier: threading.Barrier,
    worker_number: int,
) -> dict:
    barrier.wait()
    successful_latencies = []
    empty_latency = None
    claimed_ids = []
    errors = []
    while True:
        started = time.perf_counter()
        try:
            with session_factory() as db:
                job_id = acquire_pending_job(
                    db,
                    worker_id=f"queue-benchmark-{worker_number}",
                    lease_seconds=300,
                )
        except (OSError, SQLAlchemyError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            break
        elapsed = time.perf_counter() - started
        if job_id is None:
            empty_latency = elapsed
            break
        claimed_ids.append(str(job_id))
        successful_latencies.append(elapsed)
    return {
        "worker_number": worker_number,
        "claimed_ids": claimed_ids,
        "successful_latencies": successful_latencies,
        "empty_latency": empty_latency,
        "errors": errors,
    }


def audit_run(session_factory: sessionmaker, video_ids: list[uuid.UUID]) -> dict:
    with session_factory() as db:
        status_counts = dict(
            db.execute(
                select(ProcessingJob.status, func.count(ProcessingJob.id))
                .where(ProcessingJob.video_id.in_(video_ids))
                .group_by(ProcessingJob.status)
            ).all()
        )
        duplicate_starts = list(
            db.execute(
                select(ProcessingEvent.job_id, func.count(ProcessingEvent.id))
                .where(
                    ProcessingEvent.video_id.in_(video_ids),
                    ProcessingEvent.event_type == "JOB_STARTED",
                )
                .group_by(ProcessingEvent.job_id)
                .having(func.count(ProcessingEvent.id) > 1)
            ).all()
        )
    return {
        "status_counts": status_counts,
        "jobs_with_duplicate_started_events": len(duplicate_starts),
    }


def run_once(engine, session_factory, concurrency: int, jobs: int) -> dict:
    video_ids = seed_jobs(session_factory, jobs)
    try:
        counters_before = database_counters(engine)
        barrier = threading.Barrier(concurrency)
        monitor = ConnectionMonitor(engine)
        monitor.start()
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = [
                future.result()
                for future in [
                    executor.submit(claim_until_empty, session_factory, barrier, number)
                    for number in range(1, concurrency + 1)
                ]
            ]
        duration = time.perf_counter() - started
        monitor.stop()
        counters_after = database_counters(engine)
        latencies = [
            latency for result in results for latency in result["successful_latencies"]
        ]
        empty_latencies = [
            result["empty_latency"]
            for result in results
            if result["empty_latency"] is not None
        ]
        claimed_ids = [job_id for result in results for job_id in result["claimed_ids"]]
        errors = [error for result in results for error in result["errors"]]
        audit = audit_run(session_factory, video_ids)
        duplicate_claims = len(claimed_ids) - len(set(claimed_ids))
        return {
            "concurrency": concurrency,
            "jobs": jobs,
            "batch_duration_seconds": duration,
            "claims_per_second": len(claimed_ids) / duration,
            "successful_claims": len(claimed_ids),
            "empty_claims": len(empty_latencies),
            "claim_latency_seconds": summarize(latencies),
            "empty_claim_latency_seconds": summarize(empty_latencies),
            "peak_benchmark_connections": max(monitor.samples, default=0),
            "database_counter_delta": {
                key: counters_after[key] - counters_before[key]
                for key in counters_before
            },
            "errors": errors,
            "duplicate_claims": duplicate_claims,
            "audit": audit,
            "passed": (
                len(claimed_ids) == jobs
                and duplicate_claims == 0
                and not errors
                and audit["jobs_with_duplicate_started_events"] == 0
                and audit["status_counts"] == {JobStatus.PROCESSING: jobs}
            ),
        }
    finally:
        cleanup_jobs(session_factory, video_ids)


def aggregate(concurrency: int, runs: list[dict]) -> dict:
    throughput = [run["claims_per_second"] for run in runs]
    p95 = [run["claim_latency_seconds"]["p95"] for run in runs]
    p99 = [run["claim_latency_seconds"]["p99"] for run in runs]
    return {
        "concurrency": concurrency,
        "runs": len(runs),
        "claims_per_second_mean": statistics.fmean(throughput),
        "claims_per_second_min": min(throughput),
        "claims_per_second_max": max(throughput),
        "claim_latency_p95_mean_seconds": statistics.fmean(p95),
        "claim_latency_p99_mean_seconds": statistics.fmean(p99),
        "peak_benchmark_connections": max(
            run["peak_benchmark_connections"] for run in runs
        ),
        "errors": sum(len(run["errors"]) for run in runs),
        "duplicate_claims": sum(run["duplicate_claims"] for run in runs),
        "passed": all(run["passed"] for run in runs),
    }


def find_saturation_knee(summaries: list[dict]) -> dict | None:
    for previous, current in pairwise(summaries):
        throughput_gain = (
            current["claims_per_second_mean"] / previous["claims_per_second_mean"] - 1
        )
        latency_growth = (
            current["claim_latency_p95_mean_seconds"]
            / previous["claim_latency_p95_mean_seconds"]
        )
        if throughput_gain < 0.10 and latency_growth > 1.25:
            return {
                "concurrency": current["concurrency"],
                "previous_concurrency": previous["concurrency"],
                "throughput_gain_fraction": throughput_gain,
                "p95_latency_growth_factor": latency_growth,
                "definition": "throughput gain <10% while p95 latency grows >25%",
            }
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrency", default="1,2,4,8,16,32,64")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    levels = [int(value) for value in args.concurrency.split(",")]
    if args.jobs < 1 or args.runs < 1 or any(level < 1 for level in levels):
        parser.error("jobs, runs, and concurrency levels must be positive")

    settings = get_settings()
    max_concurrency = max(levels)
    engine = create_engine(
        settings.database_url,
        pool_size=max_concurrency,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={"application_name": "streamforge-queue-benchmark"},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    compose("stop", "worker")
    try:
        with session_factory() as db:
            pending = db.scalar(
                select(ProcessingJob.id)
                .where(ProcessingJob.status == JobStatus.PENDING)
                .limit(1)
            )
            max_connections = int(db.scalar(text("SHOW max_connections")))
        if pending is not None:
            raise RuntimeError("Refusing to run with unrelated PENDING jobs")
        if max_concurrency + 2 >= max_connections:
            raise RuntimeError(
                f"Requested {max_concurrency} claimers but PostgreSQL "
                f"max_connections is {max_connections}"
            )

        raw_runs = []
        summaries = []
        for concurrency in levels:
            level_runs = []
            for run_number in range(1, args.runs + 1):
                print(
                    f"Concurrency {concurrency}: run {run_number}/{args.runs}",
                    flush=True,
                )
                run_result = run_once(engine, session_factory, concurrency, args.jobs)
                run_result["run"] = run_number
                level_runs.append(run_result)
                raw_runs.append(run_result)
            summaries.append(aggregate(concurrency, level_runs))

        result = {
            "experiment": "012-postgresql-queue-concurrency",
            "recorded_at": datetime.now(UTC).isoformat(),
            "configuration": {
                "jobs_per_run": args.jobs,
                "runs_per_level": args.runs,
                "concurrency_levels": levels,
                "postgresql_max_connections": max_connections,
                "claim_strategy": "SELECT FOR UPDATE SKIP LOCKED",
                "media_processing": False,
            },
            "saturation_knee": find_saturation_knee(summaries),
            "summary_by_concurrency": summaries,
            "runs": raw_runs,
            "passed": all(summary["passed"] for summary in summaries),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
    finally:
        engine.dispose()
        restore_normal_workers()


if __name__ == "__main__":
    main()
