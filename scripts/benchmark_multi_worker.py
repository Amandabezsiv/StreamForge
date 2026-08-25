"""Compare concurrent processing with multiple unchanged workers."""

import argparse
import json
import re
import os
import subprocess
import threading
import time
from pathlib import Path

from sqlalchemy import func, select

from benchmark_concurrent_queue import run_experiment
from benchmark_e2e import generate_fixture
from benchmark_repeated import summarize
from benchmark_sizes import FIXTURES
from streamforge.core.database import SessionLocal
from streamforge.models.processing_event import ProcessingEvent
from streamforge.models.processing_job import ProcessingJob
from streamforge.models.video_output import VideoOutput

DEFAULT_OUTPUT = Path("experiments/003-two-workers/results.json")
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
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]+)\s*", value)
    if match is None:
        raise ValueError(f"Cannot parse Docker size: {value}")
    amount, unit = match.groups()
    return float(amount) * SIZE_UNITS[unit.upper()]


def worker_container_ids(expected: int) -> list[str]:
    result = subprocess.run(
        ["docker", "compose", "ps", "-q", "worker"],
        capture_output=True,
        text=True,
        check=True,
    )
    container_ids = [line for line in result.stdout.splitlines() if line]
    if len(container_ids) != expected:
        raise RuntimeError(
            f"Expected {expected} running workers, found {len(container_ids)}"
        )
    return container_ids


def read_worker_resources(container_ids: list[str]) -> dict:
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids],
        capture_output=True,
        text=True,
        check=True,
    )
    cpu_percent = 0.0
    memory_bytes = 0.0
    block_read_bytes = 0.0
    block_write_bytes = 0.0
    containers = []
    for line in result.stdout.splitlines():
        item = json.loads(line)
        memory_used, _memory_limit = item["MemUsage"].split("/")
        block_read, block_write = item["BlockIO"].split("/")
        cpu = float(item["CPUPerc"].rstrip("%"))
        memory = parse_size(memory_used)
        read_bytes = parse_size(block_read)
        write_bytes = parse_size(block_write)
        cpu_percent += cpu
        memory_bytes += memory
        block_read_bytes += read_bytes
        block_write_bytes += write_bytes
        containers.append(
            {
                "name": item["Name"],
                "cpu_percent": cpu,
                "memory_bytes": memory,
                "block_read_bytes": read_bytes,
                "block_write_bytes": write_bytes,
            }
        )
    return {
        "recorded_monotonic": time.monotonic(),
        "cpu_percent": cpu_percent,
        "memory_bytes": memory_bytes,
        "block_read_bytes": block_read_bytes,
        "block_write_bytes": block_write_bytes,
        "containers": containers,
    }


class ResourceMonitor:
    def __init__(self, container_ids: list[str], interval: float = 0.5) -> None:
        self.container_ids = container_ids
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
            self.samples.append(read_worker_resources(self.container_ids))
        except Exception as exc:
            self.errors.append(str(exc))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(read_worker_resources(self.container_ids))
            except Exception as exc:
                self.errors.append(str(exc))
            self._stop.wait(self.interval)

    def summary(self) -> dict:
        if not self.samples:
            raise RuntimeError("No Docker resource samples were collected")
        cpu = [sample["cpu_percent"] for sample in self.samples]
        memory = [sample["memory_bytes"] for sample in self.samples]
        first = self.samples[0]
        last = self.samples[-1]
        observed_interval = (
            (last["recorded_monotonic"] - first["recorded_monotonic"])
            / (len(self.samples) - 1)
            if len(self.samples) > 1
            else 0.0
        )
        return {
            "sample_count": len(self.samples),
            "requested_sample_interval_seconds": self.interval,
            "observed_mean_sample_interval_seconds": observed_interval,
            "cpu_percent_workers_total": summarize(cpu),
            "memory_bytes_workers_total": summarize(memory),
            "memory_mib_workers_total": {
                key: value / (1024**2) if key != "count" else value
                for key, value in summarize(memory).items()
            },
            "block_io_bytes_workers_total": {
                "read_delta": max(
                    0.0, last["block_read_bytes"] - first["block_read_bytes"]
                ),
                "write_delta": max(
                    0.0, last["block_write_bytes"] - first["block_write_bytes"]
                ),
                "read_start": first["block_read_bytes"],
                "read_end": last["block_read_bytes"],
                "write_start": first["block_write_bytes"],
                "write_end": last["block_write_bytes"],
            },
            "sampling_errors": self.errors,
            "raw_samples": self.samples,
        }


def audit_duplicates(video_ids: list[str]) -> dict:
    with SessionLocal() as db:
        jobs = list(
            db.scalars(select(ProcessingJob).where(ProcessingJob.video_id.in_(video_ids)))
        )
        job_ids = [job.id for job in jobs]
        starts = db.execute(
            select(ProcessingEvent.job_id, func.count(ProcessingEvent.id))
            .where(
                ProcessingEvent.job_id.in_(job_ids),
                ProcessingEvent.event_type == "JOB_STARTED",
            )
            .group_by(ProcessingEvent.job_id)
        ).all()
        output_counts = db.execute(
            select(VideoOutput.video_id, VideoOutput.type, func.count(VideoOutput.id))
            .where(VideoOutput.video_id.in_(video_ids))
            .group_by(VideoOutput.video_id, VideoOutput.type)
        ).all()
        job_counts = db.execute(
            select(ProcessingJob.video_id, func.count(ProcessingJob.id))
            .where(ProcessingJob.video_id.in_(video_ids))
            .group_by(ProcessingJob.video_id)
        ).all()

    duplicate_starts = [
        {"job_id": str(job_id), "job_started_events": count}
        for job_id, count in starts
        if count > 1
    ]
    duplicate_outputs = [
        {"video_id": str(video_id), "output_type": str(kind), "count": count}
        for video_id, kind, count in output_counts
        if count > 1
    ]
    duplicate_jobs = [
        {"video_id": str(video_id), "job_count": count}
        for video_id, count in job_counts
        if count > 1
    ]
    return {
        "detected": bool(duplicate_starts or duplicate_outputs or duplicate_jobs),
        "duplicate_job_starts": duplicate_starts,
        "duplicate_outputs": duplicate_outputs,
        "multiple_jobs_for_video": duplicate_jobs,
        "jobs_audited": len(jobs),
    }


def scale_workers(count: int, ffmpeg_threads: int) -> list[str]:
    environment = os.environ.copy()
    environment["FFMPEG_THREADS"] = str(ffmpeg_threads)
    subprocess.run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--scale",
            f"worker={count}",
            "worker",
        ],
        check=True,
        env=environment,
    )
    time.sleep(1.0)
    return worker_container_ids(count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--ffmpeg-threads",
        default="auto",
        help="Threads per FFmpeg process: 'auto' or a positive integer",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.workers < 1 or args.concurrency < 1:
        parser.error("--workers and --concurrency must be at least 1")
    if args.ffmpeg_threads == "auto":
        ffmpeg_threads = 0
        ffmpeg_threads_label: str | int = "auto"
    else:
        try:
            ffmpeg_threads = int(args.ffmpeg_threads)
        except ValueError:
            parser.error("--ffmpeg-threads must be 'auto' or a positive integer")
        if ffmpeg_threads < 1:
            parser.error("--ffmpeg-threads must be 'auto' or a positive integer")
        ffmpeg_threads_label = ffmpeg_threads

    profile = FIXTURES["medium"]
    video_path = Path("storage/benchmark-fixtures") / profile["filename"]
    if not video_path.exists():
        generate_fixture(
            video_path,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    container_ids = scale_workers(args.workers, ffmpeg_threads)
    monitor = ResourceMonitor(container_ids)
    monitor.start()
    try:
        result = run_experiment(
            args.api_url, video_path, args.concurrency, args.timeout
        )
    finally:
        monitor.stop()

    result["benchmark"] = "003-multiple-workers"
    result["configuration"]["worker_count"] = args.workers
    result["configuration"]["ffmpeg_threads_per_process"] = ffmpeg_threads_label
    batch_duration = result["batch"]["all_videos_ready_seconds"]
    result["batch"]["batch_duration_seconds"] = batch_duration
    result["batch"]["videos_per_minute"] = (
        result["batch"]["completed"] / batch_duration * 60
    )
    result["resources"] = monitor.summary()
    result["duplicate_processing"] = audit_duplicates(
        [video["video_id"] for video in result["videos_by_queue_position"]]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

    if (
        result["batch"]["failed"]
        or result["duplicate_processing"]["detected"]
        or result["resources"]["sampling_errors"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
