"""Force concurrent PostgreSQL job claims and audit their atomicity."""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from streamforge.core.database import SessionLocal
from streamforge.models import ProcessingEvent, ProcessingJob, Video
from streamforge.models.types import JobStatus
from streamforge.workers.processor import acquire_pending_job


DEFAULT_OUTPUT = Path("experiments/006-concurrent-job-acquisition/results.json")


def create_pending_jobs(count: int) -> list[uuid.UUID]:
    experiment_id = uuid.uuid4()
    with SessionLocal() as db:
        jobs: list[ProcessingJob] = []
        for index in range(count):
            video = Video(
                original_filename=f"experiment-006-{index}.mp4",
                storage_key=f"experiments/006/{experiment_id}/{index}.mp4",
                size_bytes=0,
            )
            job = ProcessingJob(video=video, status=JobStatus.PENDING)
            db.add(job)
            jobs.append(job)
        db.commit()
        return [job.id for job in jobs]


def contend(
    contender: int, barrier: threading.Barrier, lock_hold_seconds: float
) -> dict:
    barrier.wait()
    started = time.perf_counter()
    with SessionLocal() as db:
        job_id = acquire_pending_job(
            db, diagnostic_lock_hold_seconds=lock_hold_seconds
        )
    return {
        "contender": contender,
        "claimed_job_id": str(job_id) if job_id else None,
        "duration_seconds": time.perf_counter() - started,
    }


def audit(job_ids: list[uuid.UUID]) -> dict:
    with SessionLocal() as db:
        jobs = list(
            db.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids)))
        )
        starts = dict(
            db.execute(
                select(ProcessingEvent.job_id, func.count(ProcessingEvent.id))
                .where(
                    ProcessingEvent.job_id.in_(job_ids),
                    ProcessingEvent.event_type == "JOB_STARTED",
                )
                .group_by(ProcessingEvent.job_id)
            ).all()
        )
    return {
        "job_statuses": {str(job.id): job.status for job in jobs},
        "job_started_events": {
            str(job_id): starts.get(job_id, 0) for job_id in job_ids
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make concurrent sessions compete for a small pending-job set"
    )
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--contenders", type=int, default=16)
    parser.add_argument("--lock-hold-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.jobs < 1 or args.contenders < 2:
        parser.error("--jobs must be >= 1 and --contenders must be >= 2")
    if args.contenders <= args.jobs:
        parser.error("--contenders must be greater than --jobs")
    if args.lock_hold_seconds < 0:
        parser.error("--lock-hold-seconds must be >= 0")

    job_ids = create_pending_jobs(args.jobs)
    barrier = threading.Barrier(args.contenders)
    batch_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.contenders) as executor:
        attempts = [
            future.result()
            for future in [
                executor.submit(contend, index, barrier, args.lock_hold_seconds)
                for index in range(args.contenders)
            ]
        ]
    batch_duration = time.perf_counter() - batch_started

    claimed = [
        attempt["claimed_job_id"]
        for attempt in attempts
        if attempt["claimed_job_id"] is not None
    ]
    counts = Counter(claimed)
    database_audit = audit(job_ids)
    duplicate_claims = {
        job_id: count for job_id, count in counts.items() if count > 1
    }
    duplicate_events = {
        job_id: count
        for job_id, count in database_audit["job_started_events"].items()
        if count > 1
    }
    expected_claims = min(args.jobs, args.contenders)
    passed = (
        len(claimed) == expected_claims
        and len(set(claimed)) == expected_claims
        and not duplicate_claims
        and not duplicate_events
        and set(database_audit["job_statuses"].values())
        == {JobStatus.PROCESSING}
    )
    result = {
        "experiment": "006-concurrent-job-acquisition",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "jobs": args.jobs,
            "contenders": args.contenders,
            "lock_hold_seconds": args.lock_hold_seconds,
            "database": "PostgreSQL",
            "claim_strategy": "SELECT FOR UPDATE SKIP LOCKED",
        },
        "batch_duration_seconds": batch_duration,
        "claims": {
            "successful": len(claimed),
            "empty": args.contenders - len(claimed),
            "unique_job_ids": len(set(claimed)),
            "duplicate_claims": duplicate_claims,
        },
        "database_audit": database_audit,
        "passed": passed,
        "attempts": sorted(attempts, key=lambda item: item["contender"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
