# ADR-003: Kubernetes-Native Observability Deployment

Status: Accepted

Date: 2026-09-02

## Context

StreamForge v0.1 ran Prometheus, Grafana, and cAdvisor through Docker Compose.
Experiment 022 moved the unchanged application behavior to Kind, so the same
signals must now follow ephemeral Pods and Kubernetes-managed container names.

## Decision

Run one Prometheus Deployment and one Grafana Deployment in the `streamforge`
namespace. Provision their configuration and dashboard through ConfigMaps and
retain their local state on separate `ReadWriteOnce` persistent claims.

Prometheus uses Kubernetes Pod discovery for the API and every Ready worker Pod.
It scrapes kubelet's built-in `/metrics/cadvisor` endpoint through the Kubernetes
API `nodes/proxy` path instead of deploying a second privileged cAdvisor agent.
This retains CPU, CFS throttling, memory, and network metrics.

Run kube-state-metrics scoped to Pods in the `streamforge` namespace to expose
`kube_pod_container_status_restarts_total`. This replaces the approximate
start-time-change restart signal used in Docker Compose.

Prometheus receives read-only discovery permissions and `nodes/proxy`. The
kube-state-metrics service account can only list and watch Pods in the
StreamForge namespace.

Prometheus and Grafana use the `Recreate` strategy. Their embedded local stores
must not be opened by two rolling replicas sharing the same `ReadWriteOnce`
volume. This is appropriate for the current single-replica laboratory, not a
high-availability monitoring design.

## Consequences

- Application metrics follow worker Pod creation and deletion automatically.
- Container metrics do not require another privileged DaemonSet in Kind.
- Restart counts come from Kubernetes state rather than inference.
- Grafana keeps the same 14-panel structure with Kubernetes pod/container labels.
- Prometheus can proxy node metrics, which is sensitive RBAC access and should
  remain read-only and narrowly assigned.
- Prometheus and Grafana are single points of observability failure.
- Anonymous Grafana access remains suitable only for the local cluster.

## Revisit When

- the cluster gains multiple nodes;
- monitoring needs high availability or remote durable storage;
- Prometheus Operator and ServiceMonitor resources reduce operational cost;
- cluster security policy disallows kubelet proxy scraping;
- Grafana authentication or ingress is introduced.
