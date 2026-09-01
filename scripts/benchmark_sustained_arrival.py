"""Find the arrival rate where the four-worker queue begins sustained growth."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_multi_worker import audit_duplicates
from benchmark_observed_high_load import (
    StackResourceMonitor,
    configure_stack,
    container_roles,
    prometheus_snapshot,
)
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from streamforge.core.database import engine
from streamforge.models import ProcessingJob
from streamforge.models.types import JobStatus

DEFAULT_OUTPUT = Path("experiments/020-sustained-arrival-capacity/results.json")
DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-medium.mp4")


class QueueMonitor:
    def __init__(self, session_factory: sessionmaker, interval: float = 1.0) -> None:
        self.session_factory = session_factory
        self.interval = interval
        self.started = time.monotonic()
        self.samples: list[dict] = []
        self.video_ids: list[uuid.UUID] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def add_video(self, video_id: str) -> None:
        with self._lock:
            self.video_ids.append(uuid.UUID(video_id))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self.sample()

    def sample(self) -> dict:
        with self._lock:
            video_ids = list(self.video_ids)
        counts = {}
        if video_ids:
            with self.session_factory() as db:
                counts = dict(
                    db.execute(
                        select(ProcessingJob.status, func.count(ProcessingJob.id))
                        .where(ProcessingJob.video_id.in_(video_ids))
                        .group_by(ProcessingJob.status)
                    ).all()
                )
        sample = {
            "elapsed_seconds": time.monotonic() - self.started,
            "submitted": len(video_ids),
            "pending": counts.get(JobStatus.PENDING, 0),
            "processing": counts.get(JobStatus.PROCESSING, 0),
            "completed": counts.get(JobStatus.COMPLETED, 0),
            "failed": counts.get(JobStatus.FAILED, 0),
        }
        self.samples.append(sample)
        return sample

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)


def linear_slope_per_minute(samples: list[dict], field: str) -> float:
    if len(samples) < 2:
        return 0.0
    x_values = [sample["elapsed_seconds"] for sample in samples]
    y_values = [float(sample[field]) for sample in samples]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        return 0.0
    slope_per_second = (
        sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, y_values, strict=True)
        )
        / denominator
    )
    return slope_per_second * 60


def upload_video(client: httpx.Client, fixture: Path, rate: float, number: int) -> dict:
    started = time.perf_counter()
    with fixture.open("rb") as video_file:
        response = client.post(
            "/api/v1/videos",
            files={
                "file": (
                    f"sustained-{rate:g}-{number:03d}.mp4",
                    video_file,
                    "video/mp4",
                )
            },
        )
    duration = time.perf_counter() - started
    response.raise_for_status()
    return {
        "number": number,
        "video_id": response.json()["video_id"],
        "upload_duration_seconds": duration,
    }


def wait_until_terminal(
    session_factory: sessionmaker,
    video_ids: list[uuid.UUID],
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as db:
            terminal = db.scalar(
                select(func.count(ProcessingJob.id)).where(
                    ProcessingJob.video_id.in_(video_ids),
                    ProcessingJob.status.in_((JobStatus.COMPLETED, JobStatus.FAILED)),
                )
            )
        if terminal == len(video_ids):
            return
        time.sleep(0.5)
    raise TimeoutError("Sustained-arrival scenario did not drain")


def run_rate(
    *,
    rate: float,
    duration: float,
    workers: int,
    api_url: str,
    prometheus_url: str,
    fixture: Path,
    timeout: float,
    session_factory: sessionmaker,
    roles: dict[str, str],
) -> dict:
    count = max(1, math.floor(rate * duration / 60))
    interval = 60 / rate
    monitor = QueueMonitor(session_factory)
    resources = StackResourceMonitor(roles)
    uploads = []
    prometheus_before = prometheus_snapshot(prometheus_url)
    disk_before = shutil.disk_usage(fixture.parent)
    monitor.start()
    resources.start()
    started = time.monotonic()
    with httpx.Client(base_url=api_url, timeout=120.0) as client:
        for number in range(1, count + 1):
            scheduled = started + (number - 1) * interval
            remaining = scheduled - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            upload = upload_video(client, fixture, rate, number)
            upload["submitted_at_seconds"] = time.monotonic() - started
            uploads.append(upload)
            monitor.add_video(upload["video_id"])

    arrival_remaining = started + duration - time.monotonic()
    if arrival_remaining > 0:
        time.sleep(arrival_remaining)
    arrival_elapsed = time.monotonic() - started
    arrival_end_sample = monitor.sample()
    ids = [uuid.UUID(upload["video_id"]) for upload in uploads]
    wait_until_terminal(session_factory, ids, timeout)
    drained_elapsed = time.monotonic() - started
    resources.stop()
    monitor.stop()
    time.sleep(6.0)
    prometheus_after = prometheus_snapshot(prometheus_url)
    disk_after = shutil.disk_usage(fixture.parent)

    with session_factory() as db:
        jobs = list(
            db.scalars(
                select(ProcessingJob)
                .where(ProcessingJob.video_id.in_(ids))
                .order_by(ProcessingJob.created_at)
            )
        )

    arrival_samples = [
        sample for sample in monitor.samples if sample["elapsed_seconds"] <= duration
    ]
    steady_samples = [
        sample
        for sample in arrival_samples
        if sample["elapsed_seconds"] >= duration * 0.2
    ]
    pending_slope = linear_slope_per_minute(steady_samples, "pending")
    actual_rate = count / arrival_elapsed * 60
    stable = pending_slope <= 0.5 and arrival_end_sample["pending"] <= workers
    return {
        "target_arrival_rate_videos_per_minute": rate,
        "actual_arrival_rate_videos_per_minute": actual_rate,
        "scheduled_videos": count,
        "arrival_duration_seconds": arrival_elapsed,
        "total_duration_until_drained_seconds": drained_elapsed,
        "post_arrival_drain_seconds": max(0.0, drained_elapsed - arrival_elapsed),
        "queue": {
            "pending_at_arrival_end": arrival_end_sample["pending"],
            "maximum_pending": max(sample["pending"] for sample in monitor.samples),
            "growth_slope_jobs_per_minute": pending_slope,
            "classified_stable": stable,
            "samples": monitor.samples,
        },
        "jobs": {
            "completed": sum(job.status == JobStatus.COMPLETED for job in jobs),
            "failed": sum(job.status == JobStatus.FAILED for job in jobs),
            "queue_wait_seconds": summarize(
                [
                    job.queue_wait_seconds
                    for job in jobs
                    if job.queue_wait_seconds is not None
                ]
            ),
            "processing_duration_seconds": summarize(
                [
                    job.processing_duration_seconds
                    for job in jobs
                    if job.processing_duration_seconds is not None
                ]
            ),
            "errors": [
                {
                    "job_id": str(job.id),
                    "code": job.error_code,
                    "message": job.error_message,
                }
                for job in jobs
                if job.error_code is not None
            ],
        },
        "uploads": {
            "duration_seconds": summarize(
                [upload["upload_duration_seconds"] for upload in uploads]
            ),
            "samples": uploads,
        },
        "resources": resources.summary(),
        "disk": {
            "free_bytes_before": disk_before.free,
            "free_bytes_after": disk_after.free,
            "consumed_bytes": max(0, disk_before.free - disk_after.free),
        },
        "prometheus_counter_delta": {
            metric: prometheus_after[metric] - prometheus_before[metric]
            for metric in prometheus_before
        },
        "duplicate_processing": audit_duplicates(
            [upload["video_id"] for upload in uploads]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--rates", default="12,16,18,20,22")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rates = [float(value) for value in args.rates.split(",")]
    if args.duration <= 0 or args.workers < 1 or not rates or min(rates) <= 0:
        parser.error("duration, workers, and rates must be positive")

    profile = FIXTURES["medium"]
    if not DEFAULT_FIXTURE.exists():
        generate_fixture(
            DEFAULT_FIXTURE,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    configure_stack(args.workers, 0)
    with httpx.Client(base_url=args.api_url, timeout=30.0) as client:
        wait_for_api(client, args.timeout)
    time.sleep(10.0)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        pending = db.scalar(
            select(ProcessingJob.id)
            .where(ProcessingJob.status == JobStatus.PENDING)
            .limit(1)
        )
    if pending is not None:
        raise RuntimeError("Refusing to run with unrelated PENDING jobs")
    roles = container_roles(args.workers)

    results = []
    for rate in rates:
        print(f"Measuring {rate:g} videos/minute", flush=True)
        result = run_rate(
            rate=rate,
            duration=args.duration,
            workers=args.workers,
            api_url=args.api_url,
            prometheus_url=args.prometheus_url,
            fixture=DEFAULT_FIXTURE,
            timeout=args.timeout,
            session_factory=session_factory,
            roles=roles,
        )
        results.append(result)
        print(
            f"  pending_end={result['queue']['pending_at_arrival_end']} "
            f"slope={result['queue']['growth_slope_jobs_per_minute']:.2f}/min "
            f"stable={result['queue']['classified_stable']}",
            flush=True,
        )

    stable_rates = [
        result["target_arrival_rate_videos_per_minute"]
        for result in results
        if result["queue"]["classified_stable"]
    ]
    unstable_rates = [
        result["target_arrival_rate_videos_per_minute"]
        for result in results
        if not result["queue"]["classified_stable"]
    ]
    output = {
        "experiment": "020-sustained-arrival-capacity",
        "recorded_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "workers": args.workers,
            "rates_videos_per_minute": rates,
            "arrival_duration_seconds_per_rate": args.duration,
            "fixture": str(DEFAULT_FIXTURE),
            "fixture_size_bytes": DEFAULT_FIXTURE.stat().st_size,
            "listen_notify_enabled": True,
            "lease_recovery_enabled": True,
            "prometheus_enabled": True,
            "grafana_enabled": True,
        },
        "capacity_bound": {
            "highest_stable_tested_rate": max(stable_rates, default=None),
            "lowest_unstable_tested_rate": min(unstable_rates, default=None),
        },
        "rates": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))

    failed = any(result["jobs"]["failed"] for result in results)
    duplicates = any(result["duplicate_processing"]["detected"] for result in results)
    resource_errors = any(result["resources"]["sampling_errors"] for result in results)
    if failed or duplicates or resource_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
