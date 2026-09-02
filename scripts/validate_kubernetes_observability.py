"""Validate in-cluster Prometheus discovery, metrics, and Grafana provisioning."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_OUTPUT = Path("experiments/024-kubernetes-observability/results.json")
METRIC_QUERIES = {
    "jobs_pending": "count(streamforge_jobs_pending)",
    "jobs_processing": "count(streamforge_jobs_processing)",
    "worker_processing_histograms": (
        "count(streamforge_job_processing_duration_seconds_count)"
    ),
    "container_cpu": (
        'count(container_cpu_usage_seconds_total{namespace="streamforge",'
        'container!="",image!=""})'
    ),
    "container_throttling": (
        'count(container_cpu_cfs_throttled_seconds_total{namespace="streamforge",'
        'container!="",image!=""})'
    ),
    "container_memory": (
        'count(container_memory_working_set_bytes{namespace="streamforge",'
        'container!="",image!=""})'
    ),
    "pod_network": (
        'count(container_network_receive_bytes_total{namespace="streamforge",'
        'pod!=""})'
    ),
    "container_restarts": (
        'count(kube_pod_container_status_restarts_total{namespace="streamforge"})'
    ),
}


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


def wait_until_ready(client: httpx.Client, path: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get(path).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Endpoint did not become ready: {path}")


def prometheus_query(client: httpx.Client, query: str) -> dict[str, Any]:
    response = client.get("/api/v1/query", params={"query": query})
    response.raise_for_status()
    payload = response.json()
    if payload["status"] != "success":
        raise RuntimeError(f"Prometheus query failed: {query}")
    return payload


def scalar(client: httpx.Client, query: str) -> float:
    result = prometheus_query(client, query)["data"]["result"]
    return float(result[0]["value"][1]) if result else 0.0


def kubectl_json(*arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        ["kubectl", *arguments, "-o", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="streamforge")
    parser.add_argument("--prometheus-port", type=int, default=19090)
    parser.add_argument("--grafana-port", type=int, default=13000)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    prometheus_forward = PortForward(
        args.namespace,
        "service/prometheus",
        f"{args.prometheus_port}:9090",
    )
    grafana_forward = PortForward(
        args.namespace,
        "service/grafana",
        f"{args.grafana_port}:3000",
    )
    try:
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{args.prometheus_port}", timeout=30
            ) as prometheus,
            httpx.Client(
                base_url=f"http://127.0.0.1:{args.grafana_port}", timeout=30
            ) as grafana,
        ):
            wait_until_ready(prometheus, "/-/ready", args.timeout)
            wait_until_ready(grafana, "/api/health", args.timeout)
            time.sleep(6)

            targets_response = prometheus.get("/api/v1/targets")
            targets_response.raise_for_status()
            targets = targets_response.json()["data"]["activeTargets"]
            target_summary = [
                {
                    "job": target["labels"]["job"],
                    "pod": target["labels"].get("pod"),
                    "node": target["labels"].get("node"),
                    "health": target["health"],
                    "last_error": target["lastError"],
                }
                for target in targets
            ]
            metric_series = {
                name: scalar(prometheus, query)
                for name, query in METRIC_QUERIES.items()
            }

            datasource_response = grafana.get("/api/datasources/uid/prometheus/health")
            datasource_response.raise_for_status()
            datasource = datasource_response.json()
            dashboard_response = grafana.get(
                "/api/dashboards/uid/streamforge-kubernetes-overview"
            )
            dashboard_response.raise_for_status()
            dashboard = dashboard_response.json()["dashboard"]
            panel_query_errors = []
            panel_query_count = 0
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    expression = target.get("expr")
                    if not expression:
                        continue
                    panel_query_count += 1
                    expression = expression.replace("$__rate_interval", "1m").replace(
                        "$__range", "1h"
                    )
                    try:
                        prometheus_query(prometheus, expression)
                    except (httpx.HTTPError, RuntimeError) as exc:
                        panel_query_errors.append(
                            {"panel": panel["title"], "error": str(exc)}
                        )
    finally:
        grafana_forward.close()
        prometheus_forward.close()

    pods = kubectl_json("get", "pods", f"--namespace={args.namespace}")["items"]
    observability_pods = [
        {
            "name": pod["metadata"]["name"],
            "phase": pod["status"]["phase"],
            "ready": any(
                condition["type"] == "Ready" and condition["status"] == "True"
                for condition in pod["status"].get("conditions", [])
            ),
            "restarts": sum(
                status["restartCount"]
                for status in pod["status"].get("containerStatuses", [])
            ),
        }
        for pod in pods
        if pod["metadata"]["labels"].get("app.kubernetes.io/name")
        in {"prometheus", "grafana", "kube-state-metrics"}
    ]
    jobs = [target["job"] for target in target_summary if target["health"] == "up"]
    passed = (
        len(target_summary) == 7
        and all(target["health"] == "up" for target in target_summary)
        and jobs.count("streamforge-api") == 1
        and jobs.count("streamforge-worker") == 4
        and jobs.count("kube-state-metrics") == 1
        and jobs.count("kubelet-cadvisor") == 1
        and all(count > 0 for count in metric_series.values())
        and datasource["status"] == "OK"
        and len(dashboard["panels"]) == 14
        and panel_query_count == 15
        and not panel_query_errors
        and len(observability_pods) == 3
        and all(pod["ready"] for pod in observability_pods)
    )
    result = {
        "experiment": "024-kubernetes-observability",
        "recorded_at": datetime.now(UTC).isoformat(),
        "targets": target_summary,
        "metric_series_counts": metric_series,
        "grafana": {
            "datasource_status": datasource["status"],
            "datasource_message": datasource["message"],
            "dashboard_uid": dashboard["uid"],
            "dashboard_title": dashboard["title"],
            "panel_count": len(dashboard["panels"]),
            "panel_query_count": panel_query_count,
            "panel_query_errors": panel_query_errors,
        },
        "observability_pods": observability_pods,
        "passed": passed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
