"""Delete a transcoding worker Pod and measure Kubernetes and lease recovery."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from benchmark_e2e import generate_fixture
from benchmark_sizes import FIXTURES
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from streamforge.models import ProcessingEvent, ProcessingJob, Video, VideoOutput
from streamforge.models.types import JobStatus, VideoStatus

DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-medium.mp4")
DEFAULT_OUTPUT = Path("experiments/023-kubernetes-worker-pod-failure/results.json")
METRIC_PATTERN = re.compile(
    r"^(streamforge_worker_lease_expired_total|streamforge_job_retries_total) "
    r"([0-9.eE+-]+)$",
    re.MULTILINE,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def seconds_between(start: datetime, end: datetime | None) -> float | None:
    if end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def kubectl(*arguments: str, output: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", *arguments],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout if output else ""


def kubectl_json(*arguments: str) -> dict[str, Any]:
    return json.loads(kubectl(*arguments, "-o", "json"))


class PortForward:
    def __init__(self, namespace: str, resource: str, ports: str) -> None:
        self.process = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                f"--namespace={namespace}",
                resource,
                ports,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError("API port-forward did not become ready")


def worker_pods(namespace: str) -> list[dict[str, Any]]:
    response = kubectl_json(
        "get",
        "pods",
        f"--namespace={namespace}",
        "--selector=app.kubernetes.io/name=worker",
    )
    return response["items"]


def pod_ready(pod: dict[str, Any]) -> bool:
    return any(
        condition["type"] == "Ready" and condition["status"] == "True"
        for condition in pod["status"].get("conditions", [])
    )


def wait_for_transcoding(
    session_factory: sessionmaker, video_id: uuid.UUID, timeout: float
) -> tuple[ProcessingJob, ProcessingEvent]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as db:
            event = db.scalar(
                select(ProcessingEvent)
                .where(
                    ProcessingEvent.video_id == video_id,
                    ProcessingEvent.event_type == "TRANSCODING_STARTED",
                )
                .order_by(ProcessingEvent.created_at.desc())
            )
            if event is not None:
                job = db.get(ProcessingJob, event.job_id)
                if job is not None and job.claimed_by is not None:
                    db.expunge(job)
                    db.expunge(event)
                    return job, event
        time.sleep(0.05)
    raise TimeoutError("TRANSCODING_STARTED was not observed")


def list_temporary_files(namespace: str, video_id: uuid.UUID) -> list[str]:
    script = (
        "from pathlib import Path; "
        f"root=Path('/app/storage/videos/{video_id}'); "
        "print('\\n'.join(sorted(str(p) for p in root.glob('.*.tmp'))))"
    )
    result = kubectl(
        "exec",
        f"--namespace={namespace}",
        "deployment/streamforge-api",
        "--",
        "python",
        "-c",
        script,
    )
    return [line for line in result.splitlines() if line]


def collect_worker_metrics(namespace: str) -> dict[str, float]:
    totals = {
        "streamforge_worker_lease_expired_total": 0.0,
        "streamforge_job_retries_total": 0.0,
    }
    script = (
        "from urllib.request import urlopen; "
        "print(urlopen('http://127.0.0.1:9000/metrics').read().decode())"
    )
    for pod in worker_pods(namespace):
        text = kubectl(
            "exec",
            f"--namespace={namespace}",
            pod["metadata"]["name"],
            "--",
            "python",
            "-c",
            script,
        )
        for name, value in METRIC_PATTERN.findall(text):
            totals[name] += float(value)
    return totals


def serialize_job(job: ProcessingJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "attempt": job.attempt,
        "status": job.status,
        "claimed_by": job.claimed_by,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "queue_wait_seconds": job.queue_wait_seconds,
        "processing_duration_seconds": job.processing_duration_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="streamforge")
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--database-port", type=int, default=15432)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    profile = FIXTURES["medium"]
    if not DEFAULT_FIXTURE.exists():
        generate_fixture(
            DEFAULT_FIXTURE,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    initial_pods = worker_pods(args.namespace)
    if len(initial_pods) != 4 or not all(pod_ready(pod) for pod in initial_pods):
        raise RuntimeError("Experiment requires exactly four Ready worker Pods")
    initial_uids = {pod["metadata"]["uid"] for pod in initial_pods}
    metrics_before = collect_worker_metrics(args.namespace)

    api_forward = PortForward(
        args.namespace, "service/streamforge-api", f"{args.api_port}:8000"
    )
    database_forward = PortForward(
        args.namespace, "service/postgres", f"{args.database_port}:5432"
    )
    engine = create_engine(
        "postgresql+psycopg://streamforge:streamforge@"
        f"127.0.0.1:{args.database_port}/streamforge?connect_timeout=3",
        pool_pre_ping=True,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{args.api_port}", timeout=120.0
        ) as client:
            wait_for_api(client, args.timeout)
            with DEFAULT_FIXTURE.open("rb") as fixture_file:
                response = client.post(
                    "/api/v1/videos",
                    files={"file": (DEFAULT_FIXTURE.name, fixture_file, "video/mp4")},
                )
            response.raise_for_status()
            video_id = uuid.UUID(response.json()["video_id"])
            original_job, transcode_event = wait_for_transcoding(
                session_factory, video_id, args.timeout
            )
            owner_id = original_job.claimed_by
            if owner_id is None:
                raise RuntimeError("Job owner disappeared before failure injection")
            failed_pod_name = owner_id.rsplit("-", 1)[0]
            if failed_pod_name not in {pod["metadata"]["name"] for pod in initial_pods}:
                raise RuntimeError(f"Job owner Pod not found: {failed_pod_name}")
            print(
                f"Deleting owner Pod {failed_pod_name} during transcoding",
                flush=True,
            )

            deletion_wall_time = utc_now()
            deletion_monotonic = time.monotonic()
            kubectl(
                "delete",
                "pod",
                failed_pod_name,
                f"--namespace={args.namespace}",
                "--grace-period=0",
                "--force",
                "--wait=false",
            )

            replacement_created_observed = None
            replacement_ready_observed = None
            replacement_pod: dict[str, Any] | None = None
            abandoned_observed = None
            retry_started_observed = None
            temporary_files_after_crash: list[str] = []
            temporary_snapshot_taken = False
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                now = time.monotonic()
                pods = worker_pods(args.namespace)
                new_pods = [
                    pod for pod in pods if pod["metadata"]["uid"] not in initial_uids
                ]
                if new_pods and replacement_created_observed is None:
                    replacement_pod = new_pods[0]
                    replacement_created_observed = now
                    print(
                        f"Replacement Pod created: {replacement_pod['metadata']['name']}",
                        flush=True,
                    )
                if (
                    replacement_pod is not None
                    and replacement_ready_observed is None
                    and any(
                        pod["metadata"]["uid"] == replacement_pod["metadata"]["uid"]
                        and pod_ready(pod)
                        for pod in pods
                    )
                ):
                    replacement_ready_observed = now
                    print("Replacement Pod is Ready", flush=True)

                with session_factory() as db:
                    jobs = list(
                        db.scalars(
                            select(ProcessingJob)
                            .where(ProcessingJob.video_id == video_id)
                            .order_by(ProcessingJob.attempt)
                        )
                    )
                    first_attempt = jobs[0]
                    if (
                        first_attempt.error_code == "WorkerLeaseExpired"
                        and abandoned_observed is None
                    ):
                        abandoned_observed = now
                        print("Expired lease registered", flush=True)
                    if (
                        len(jobs) >= 2
                        and jobs[1].started_at is not None
                        and retry_started_observed is None
                    ):
                        retry_started_observed = now
                        print("Recovery attempt started", flush=True)
                    video = db.get(Video, video_id)
                    terminal = (
                        video is not None
                        and video.status in {VideoStatus.READY, VideoStatus.FAILED}
                        and len(jobs) >= 2
                        and jobs[-1].status in {JobStatus.COMPLETED, JobStatus.FAILED}
                    )

                if not temporary_snapshot_taken and now - deletion_monotonic >= 1:
                    temporary_files_after_crash = list_temporary_files(
                        args.namespace, video_id
                    )
                    temporary_snapshot_taken = True
                if (
                    terminal
                    and replacement_ready_observed is not None
                    and abandoned_observed is not None
                    and retry_started_observed is not None
                ):
                    recovery_completed = now
                    print("Recovered video reached a terminal state", flush=True)
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("Worker Pod failure did not recover in time")

            video_response = client.get(f"/api/v1/videos/{video_id}")
            video_response.raise_for_status()
            output_response = client.get(f"/api/v1/videos/{video_id}/outputs")
            output_response.raise_for_status()

        with session_factory() as db:
            video = db.get(Video, video_id)
            jobs = list(
                db.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.video_id == video_id)
                    .order_by(ProcessingJob.attempt)
                )
            )
            events = list(
                db.scalars(
                    select(ProcessingEvent)
                    .where(ProcessingEvent.video_id == video_id)
                    .order_by(ProcessingEvent.created_at)
                )
            )
            outputs = list(
                db.scalars(select(VideoOutput).where(VideoOutput.video_id == video_id))
            )
            if video is None:
                raise RuntimeError("Video disappeared during recovery")
    finally:
        engine.dispose()
        database_forward.close()
        api_forward.close()

    final_pods = worker_pods(args.namespace)
    metrics_after = collect_worker_metrics(args.namespace)
    temporary_files_final = list_temporary_files(args.namespace, video_id)
    replacement_creation_time = datetime.fromisoformat(
        replacement_pod["metadata"]["creationTimestamp"]
    )
    replacement_ready_condition = next(
        condition
        for condition in replacement_pod["status"]["conditions"]
        if condition["type"] == "Ready"
    )
    replacement_ready_time = datetime.fromisoformat(
        replacement_ready_condition["lastTransitionTime"]
    )
    abandoned_event = next(
        event for event in events if event.event_type == "JOB_ABANDONED"
    )
    retry_job = jobs[1]
    output_keys = [output.storage_key for output in outputs]
    output_types = [str(output.type) for output in outputs]
    metrics_delta = {
        key: metrics_after[key] - metrics_before[key] for key in metrics_before
    }
    passed = (
        len(final_pods) == 4
        and all(pod_ready(pod) for pod in final_pods)
        and len(jobs) == 2
        and jobs[0].status == JobStatus.FAILED
        and jobs[0].error_code == "WorkerLeaseExpired"
        and jobs[1].status == JobStatus.COMPLETED
        and video.status == VideoStatus.READY
        and output_types.count("THUMBNAIL") == 1
        and output_types.count("TRANSCODED_VIDEO") == 1
        and len(output_keys) == len(set(output_keys))
        and not temporary_files_final
        and metrics_delta["streamforge_worker_lease_expired_total"] == 1
        and metrics_delta["streamforge_job_retries_total"] == 1
    )
    result = {
        "experiment": "023-kubernetes-worker-pod-failure",
        "recorded_at": utc_now().isoformat(),
        "configuration": {
            "workers": 4,
            "lease_seconds": 30,
            "lease_renewal_seconds": 10,
            "poll_fallback_seconds": 30,
            "failure_method": "force delete owner Pod during FFmpeg transcode",
            "fixture": str(DEFAULT_FIXTURE),
            "fixture_size_bytes": DEFAULT_FIXTURE.stat().st_size,
        },
        "failed_pod": {
            "name": failed_pod_name,
            "owner_id": owner_id,
            "deletion_requested_at": deletion_wall_time.isoformat(),
        },
        "replacement_pod": {
            "name": replacement_pod["metadata"]["name"],
            "creation_time": replacement_creation_time.isoformat(),
            "ready_time": replacement_ready_time.isoformat(),
        },
        "timings_seconds": {
            "replacement_created_after_deletion": seconds_between(
                deletion_wall_time, replacement_creation_time
            ),
            "replacement_observed_after_deletion": (
                replacement_created_observed - deletion_monotonic
            ),
            "replacement_became_ready_after_deletion": seconds_between(
                deletion_wall_time, replacement_ready_time
            ),
            "replacement_ready_observed_after_deletion": (
                replacement_ready_observed - deletion_monotonic
            ),
            "lease_expired_after_deletion": seconds_between(
                deletion_wall_time, abandoned_event.created_at
            ),
            "recovery_attempt_started_after_deletion": seconds_between(
                deletion_wall_time, retry_job.started_at
            ),
            "recovery_attempt_started_after_lease_expiry": seconds_between(
                abandoned_event.created_at, retry_job.started_at
            ),
            "video_ready_after_deletion": recovery_completed - deletion_monotonic,
            "total_upload_to_ready": retry_job.total_time_to_ready_seconds,
        },
        "database": {
            "video_id": str(video_id),
            "final_video_state": video.status,
            "attempt_count": len(jobs),
            "jobs": [serialize_job(job) for job in jobs],
            "events": [
                {
                    "event_type": event.event_type,
                    "job_id": str(event.job_id),
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
        },
        "outputs": {
            "count": len(outputs),
            "types": output_types,
            "storage_keys": output_keys,
            "duplicate_storage_keys": len(output_keys) != len(set(output_keys)),
            "api_response": output_response.json(),
        },
        "temporary_files": {
            "after_crash": temporary_files_after_crash,
            "after_recovery": temporary_files_final,
        },
        "worker_metrics": {
            "before": metrics_before,
            "after": metrics_after,
            "delta": metrics_delta,
        },
        "final_worker_pods": [
            {
                "name": pod["metadata"]["name"],
                "ready": pod_ready(pod),
                "uid": pod["metadata"]["uid"],
            }
            for pod in final_pods
        ],
        "transcoding_started_at": transcode_event.created_at.isoformat(),
        "passed": passed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
