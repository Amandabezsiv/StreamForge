"""Run 50 medium videos through the fully observed four-worker stack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_concurrent_queue import run_experiment
from benchmark_e2e import generate_fixture, wait_for_api
from benchmark_idle_polling import (
    database_snapshot,
    polling_query_statistics,
    reset_query_statistics,
)
from benchmark_multi_worker import audit_duplicates, parse_size
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES
from benchmark_worker_failure import compose
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from streamforge.core.config import get_settings
from streamforge.models import ProcessingJob
from streamforge.models.types import JobStatus

DEFAULT_OUTPUT = Path("experiments/019-observed-high-load/results.json")
DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-medium.mp4")
SERVICES = ("worker", "api", "postgres", "prometheus", "grafana")
PROMETHEUS_COUNTERS = (
    "streamforge_jobs_completed_total",
    "streamforge_jobs_failed_total",
    "streamforge_job_pickup_duration_seconds_count",
    "streamforge_job_processing_duration_seconds_count",
    "streamforge_worker_lease_expired_total",
    "streamforge_job_retries_total",
)
RESOURCE_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    json.JSONDecodeError,
    KeyError,
    ValueError,
)


def configure_stack(worker_count: int, ffmpeg_threads: int) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "FFMPEG_THREADS": str(ffmpeg_threads),
            "JOB_NOTIFICATIONS_ENABLED": "true",
            "WORKER_POLL_INTERVAL": "30",
            "JOB_LEASE_SECONDS": "30",
            "JOB_LEASE_RENEWAL_SECONDS": "10",
        }
    )
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        f"worker={worker_count}",
        "worker",
        "api",
        "prometheus",
        "grafana",
        env=environment,
    )


def container_roles(worker_count: int) -> dict[str, str]:
    roles = {}
    for service in SERVICES:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q", service],
            check=True,
            text=True,
            capture_output=True,
        )
        ids = [item for item in result.stdout.splitlines() if item]
        expected = worker_count if service == "worker" else 1
        if len(ids) != expected:
            raise RuntimeError(
                f"Expected {expected} {service} containers, found {len(ids)}"
            )
        roles.update({container_id: service for container_id in ids})
    return roles


def read_resources(roles: dict[str, str]) -> dict:
    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *roles,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    totals = {
        service: {
            "cpu_percent": 0.0,
            "memory_bytes": 0.0,
            "block_read_bytes": 0.0,
            "block_write_bytes": 0.0,
        }
        for service in SERVICES
    }
    for line in result.stdout.splitlines():
        item = json.loads(line)
        container_id = next(
            candidate
            for candidate in roles
            if candidate.startswith(item["ID"]) or item["ID"].startswith(candidate[:12])
        )
        service = roles[container_id]
        memory_used, _limit = item["MemUsage"].split("/")
        block_read, block_write = item["BlockIO"].split("/")
        totals[service]["cpu_percent"] += float(item["CPUPerc"].rstrip("%"))
        totals[service]["memory_bytes"] += parse_size(memory_used)
        totals[service]["block_read_bytes"] += parse_size(block_read)
        totals[service]["block_write_bytes"] += parse_size(block_write)
    return {"recorded_monotonic": time.monotonic(), "services": totals}


class StackResourceMonitor:
    def __init__(self, roles: dict[str, str], interval: float = 0.5) -> None:
        self.roles = roles
        self.interval = interval
        self.samples: list[dict] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        try:
            self.samples.append(read_resources(self.roles))
        except RESOURCE_ERRORS as exc:
            self.errors.append(str(exc))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(read_resources(self.roles))
            except RESOURCE_ERRORS as exc:
                self.errors.append(str(exc))
            self._stop.wait(self.interval)

    def summary(self) -> dict:
        if not self.samples:
            raise RuntimeError("No Docker resource samples were collected")
        result = {}
        for service in SERVICES:
            cpu = [
                sample["services"][service]["cpu_percent"] for sample in self.samples
            ]
            memory = [
                sample["services"][service]["memory_bytes"] for sample in self.samples
            ]
            first = self.samples[0]["services"][service]
            last = self.samples[-1]["services"][service]
            result[service] = {
                "cpu_percent": summarize(cpu),
                "memory_mib": {
                    key: value / (1024**2) if key != "count" else value
                    for key, value in summarize(memory).items()
                },
                "block_io_bytes": {
                    "read_delta": max(
                        0.0,
                        last["block_read_bytes"] - first["block_read_bytes"],
                    ),
                    "write_delta": max(
                        0.0,
                        last["block_write_bytes"] - first["block_write_bytes"],
                    ),
                },
            }
        return {
            "sample_count": len(self.samples),
            "sampling_errors": self.errors,
            "by_service": result,
        }


def prometheus_scalar(api_url: str, query: str) -> float:
    active_workers = '(up{job="streamforge-worker"} == 1)'
    active_query = f"sum({query} and on(instance) {active_workers})"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{api_url}/api/v1/query",
            params={"query": active_query},
        )
        response.raise_for_status()
    results = response.json()["data"]["result"]
    return float(results[0]["value"][1]) if results else 0.0


def prometheus_snapshot(api_url: str) -> dict[str, float]:
    return {
        metric: prometheus_scalar(api_url, metric) for metric in PROMETHEUS_COUNTERS
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--videos", type=int, default=50)
    parser.add_argument("--ffmpeg-threads", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.workers < 1 or args.videos < 1 or args.ffmpeg_threads < 0:
        parser.error("workers/videos must be positive and threads cannot be negative")

    profile = FIXTURES["medium"]
    if not DEFAULT_FIXTURE.exists():
        generate_fixture(
            DEFAULT_FIXTURE,
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
        connect_args={"application_name": "streamforge-high-load-benchmark"},
    )
    session_factory = sessionmaker(bind=engine)
    configure_stack(args.workers, args.ffmpeg_threads)
    with httpx.Client(base_url=args.api_url, timeout=30.0) as client:
        wait_for_api(client, args.timeout)
    time.sleep(10.0)
    with session_factory() as db:
        pending = db.scalar(
            select(ProcessingJob.id)
            .where(ProcessingJob.status == JobStatus.PENDING)
            .limit(1)
        )
    if pending is not None:
        raise RuntimeError("Refusing to run with unrelated PENDING jobs")

    roles = container_roles(args.workers)
    reset_query_statistics(engine)
    database_before = database_snapshot(engine)
    prometheus_before = prometheus_snapshot(args.prometheus_url)
    monitor = StackResourceMonitor(roles)
    monitor.start()
    try:
        result = run_experiment(
            args.api_url,
            DEFAULT_FIXTURE,
            args.videos,
            args.timeout,
        )
    finally:
        monitor.stop()
    time.sleep(7.0)
    database_after = database_snapshot(engine)
    prometheus_after = prometheus_snapshot(args.prometheus_url)

    batch_duration = result["batch"]["all_videos_ready_seconds"]
    video_ids = [video["video_id"] for video in result["videos_by_queue_position"]]
    result["benchmark"] = "019-observed-high-load"
    result["recorded_at"] = datetime.now(UTC).isoformat()
    result["configuration"].update(
        {
            "worker_count": args.workers,
            "ffmpeg_threads_per_process": args.ffmpeg_threads or "auto",
            "listen_notify_enabled": True,
            "lease_seconds": 30,
            "lease_renewal_seconds": 10,
            "prometheus_enabled": True,
            "grafana_enabled": True,
            "host_logical_cpus": os.cpu_count(),
        }
    )
    result["batch"].update(
        {
            "batch_duration_seconds": batch_duration,
            "videos_per_minute": result["batch"]["completed"] / batch_duration * 60,
        }
    )
    result["summary_seconds"]["upload_duration"] = summarize(
        [
            video["upload_duration_seconds"]
            for video in result["videos_by_queue_position"]
        ]
    )
    result["resources"] = monitor.summary()
    result["duplicate_processing"] = audit_duplicates(video_ids)
    result["database"] = {
        "counter_delta": {
            key: database_after[key] - database_before[key]
            for key in database_before
            if key != "numbackends"
        },
        "connections_before": database_before["numbackends"],
        "connections_after": database_after["numbackends"],
        "processing_job_select_latency": polling_query_statistics(engine),
    }
    result["prometheus_counter_delta"] = {
        metric: prometheus_after[metric] - prometheus_before[metric]
        for metric in PROMETHEUS_COUNTERS
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    engine.dispose()

    if (
        result["batch"]["failed"]
        or result["duplicate_processing"]["detected"]
        or result["resources"]["sampling_errors"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
