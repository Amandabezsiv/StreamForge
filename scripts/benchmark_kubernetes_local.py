"""Validate the v0.1 processing behavior on the local Kubernetes deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from benchmark_e2e import generate_fixture
from benchmark_sizes import FIXTURES

DEFAULT_FIXTURE = Path("storage/benchmark-fixtures/baseline-small.mp4")
DEFAULT_OUTPUT = Path("experiments/022-kubernetes-local-deployment/results.json")


def kubectl_json(*arguments: str) -> dict:
    result = subprocess.run(
        ["kubectl", *arguments, "-o", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("Kubernetes API port-forward did not become ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="streamforge")
    parser.add_argument("--local-port", type=int, default=18000)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    profile = FIXTURES["small"]
    if not DEFAULT_FIXTURE.exists():
        generate_fixture(
            DEFAULT_FIXTURE,
            duration_seconds=profile["duration_seconds"],
            resolution=profile["resolution"],
            frame_rate=profile["frame_rate"],
            video_bitrate=profile["video_bitrate"],
        )

    forward = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            f"--namespace={args.namespace}",
            "service/streamforge-api",
            f"{args.local_port}:8000",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = time.monotonic()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{args.local_port}", timeout=60.0
        ) as client:
            wait_for_api(client, args.timeout)
            with DEFAULT_FIXTURE.open("rb") as fixture_file:
                response = client.post(
                    "/api/v1/videos",
                    files={"file": (DEFAULT_FIXTURE.name, fixture_file, "video/mp4")},
                )
            response.raise_for_status()
            video_id = response.json()["video_id"]

            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                video_response = client.get(f"/api/v1/videos/{video_id}")
                video_response.raise_for_status()
                video = video_response.json()
                if video["status"] in {"READY", "FAILED"}:
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Kubernetes processing did not finish")

            outputs_response = client.get(f"/api/v1/videos/{video_id}/outputs")
            outputs_response.raise_for_status()
            jobs_response = client.get(f"/api/v1/videos/{video_id}/jobs")
            jobs_response.raise_for_status()
    finally:
        forward.terminate()
        try:
            forward.wait(timeout=5)
        except subprocess.TimeoutExpired:
            forward.kill()

    outputs = outputs_response.json()
    jobs = jobs_response.json()
    pods = kubectl_json("get", "pods", f"--namespace={args.namespace}")
    result = {
        "experiment": "022-kubernetes-local-deployment",
        "recorded_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": time.monotonic() - started,
        "video": video,
        "outputs": outputs,
        "jobs": jobs,
        "cluster": {
            "namespace": args.namespace,
            "pods": [
                {
                    "name": item["metadata"]["name"],
                    "phase": item["status"]["phase"],
                    "ready": all(
                        status.get("ready", False)
                        for status in item["status"].get("containerStatuses", [])
                    ),
                    "node": item["spec"]["nodeName"],
                }
                for item in pods["items"]
            ],
        },
        "validation": {
            "video_ready": video["status"] == "READY",
            "metadata_complete": all(
                video[field] is not None
                for field in (
                    "duration_seconds",
                    "width",
                    "height",
                    "fps",
                    "codec",
                    "bitrate",
                )
            ),
            "thumbnail_registered": any(
                output["type"] == "THUMBNAIL" and output["size_bytes"] > 0
                for output in outputs
            ),
            "transcode_registered": any(
                output["type"] == "TRANSCODED_VIDEO"
                and output["resolution"] == "720p"
                and output["size_bytes"] > 0
                for output in outputs
            ),
            "job_completed": any(job["status"] == "COMPLETED" for job in jobs),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not all(result["validation"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
