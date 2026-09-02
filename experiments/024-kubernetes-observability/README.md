# Experiment 024: Kubernetes Observability

## Question

Can the Docker Compose observability signals move into Kubernetes while
following dynamic API and worker Pods and preserving the existing dashboard?

## Architecture

```text
API Pod :8000/metrics ────────────────┐
                                      │ Kubernetes Pod discovery
Worker Pods :9000/metrics ────────────┤
                                      ▼
kubelet /metrics/cadvisor ─────▶ Prometheus Pod + PVC
                                      ▲
kube-state-metrics ───────────────────┘
                                      │
                                      ▼
                              Grafana Pod + PVC
                              provisioned dashboard
```

## Components

- Prometheus 3.14.0 Deployment, Service, ConfigMap, and 1 GiB PVC
- Grafana 13.2.0 Deployment, Service, provisioning ConfigMaps, and 1 GiB PVC
- kube-state-metrics 2.20.0 scoped to StreamForge Pods
- Kubernetes service-account discovery and kubelet-proxy RBAC
- API and worker Pod scraping
- kubelet cAdvisor CPU, throttling, memory, and network series
- Kubernetes restart counters
- 14-panel StreamForge Kubernetes dashboard

Prometheus retains two days or at most 512 MB. Prometheus and Grafana use
`Recreate`, preventing two replicas from opening the same local data volume
during configuration rollouts.

## Deploy

```bash
./kubernetes/observability/deploy.sh
```

The script creates or updates all provisioning ConfigMaps, applies the
resources, safely restarts Prometheus and Grafana, and waits for every rollout.

## Validate

```bash
uv run python scripts/validate_kubernetes_observability.py
```

The validator checks target health, required metric series, all dashboard
queries, datasource health, panel count, and observability Pod readiness.

## Access

```bash
kubectl port-forward -n streamforge service/prometheus 9090:9090
kubectl port-forward -n streamforge service/grafana 3000:3000
```

Then open Prometheus at <http://localhost:9090> or Grafana at
<http://localhost:3000/d/streamforge-kubernetes-overview/streamforge-kubernetes-overview>.

## Result

Recorded on 2026-09-02. The deployment and repeatable validator completed
successfully.

### Scrape targets

| Job | Expected targets | Healthy |
| --- | ---: | ---: |
| StreamForge API | 1 | 1 |
| StreamForge workers | 4 | 4 |
| kubelet cAdvisor | 1 node | 1 |
| kube-state-metrics | 1 | 1 |
| **Total** | **7** | **7** |

Kubernetes Pod discovery found the existing API and all four dynamically named
worker Pods. No static Pod IPs are stored in Prometheus configuration.

### Metric availability

| Signal | Series found |
| --- | ---: |
| Pending-job gauge | 1 |
| Processing-job gauge | 1 |
| Worker processing histograms | 4 |
| Container CPU | 14 |
| Container CFS throttling | 14 |
| Container memory | 14 |
| Pod network receive | 13 |
| Kubernetes container restart counters | 11 |

Series counts describe the current targets and containers; they are not metric
values. Kubelet cAdvisor supplied CPU, throttling, memory, and network data
through the Kubernetes API proxy. kube-state-metrics supplied restart counters.

### Grafana

| Check | Result |
| --- | --- |
| Prometheus datasource | `OK` |
| Dashboard | `StreamForge Kubernetes Overview` |
| Panels | 14 |
| PromQL expressions validated | 15 |
| Query errors | 0 |

The dashboard retains all nine application panels and five container panels.
The restart panel now uses the Kubernetes restart counter rather than inferred
cAdvisor start-time changes.

### Pod health

Prometheus, Grafana, and kube-state-metrics each finished with one Ready Pod and
zero restarts. Prometheus and Grafana configuration rollouts were also tested
with `Recreate`, avoiding concurrent access to their single-replica persistent
stores.

## Conclusion

The Docker Compose observability behavior now runs inside Kubernetes. Pod
discovery automatically follows worker replacement, the kubelet provides the
retained cAdvisor signals without another privileged container, and Grafana is
fully provisioned from version-controlled configuration.

This remains a local, single-replica monitoring stack. High availability,
authentication, ingress, alerting, and durable remote Prometheus storage are
future concerns.

Raw validation output is stored in [results.json](results.json).
