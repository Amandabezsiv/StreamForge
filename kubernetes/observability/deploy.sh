#!/usr/bin/env bash
set -euo pipefail

namespace=streamforge
manifest_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$manifest_dir/../.." && pwd)
dashboard_file=$(mktemp /tmp/streamforge-kubernetes-dashboard.XXXXXX.json)
trap 'rm -f "$dashboard_file"' EXIT

for command_name in jq kubectl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 1
  fi
done

kubectl get namespace "$namespace" >/dev/null

jq '
  .title = "StreamForge Kubernetes Overview"
  | .uid = "streamforge-kubernetes-overview"
  | .version = 1
  | (.panels[] | select(.id == 10).targets[0].expr) = "sum by (pod, container) (rate(container_cpu_usage_seconds_total{namespace=\"streamforge\",container!=\"\",image!=\"\"}[$__rate_interval])) * 100"
  | (.panels[] | select(.id == 10).targets[0].legendFormat) = "{{pod}} / {{container}}"
  | (.panels[] | select(.id == 11).targets[0].expr) = "sum by (pod, container) (rate(container_cpu_cfs_throttled_seconds_total{namespace=\"streamforge\",container!=\"\",image!=\"\"}[$__rate_interval]))"
  | (.panels[] | select(.id == 11).targets[0].legendFormat) = "{{pod}} / {{container}} throttled s/s"
  | (.panels[] | select(.id == 12).targets[0].expr) = "sum by (pod, container) (container_memory_working_set_bytes{namespace=\"streamforge\",container!=\"\",image!=\"\"})"
  | (.panels[] | select(.id == 12).targets[0].legendFormat) = "{{pod}} / {{container}}"
  | (.panels[] | select(.id == 13).targets[0].expr) = "sum by (pod) (rate(container_network_receive_bytes_total{namespace=\"streamforge\",pod!=\"\"}[$__rate_interval]))"
  | (.panels[] | select(.id == 13).targets[0].legendFormat) = "{{pod}} receive"
  | (.panels[] | select(.id == 13).targets[1].expr) = "sum by (pod) (rate(container_network_transmit_bytes_total{namespace=\"streamforge\",pod!=\"\"}[$__rate_interval]))"
  | (.panels[] | select(.id == 13).targets[1].legendFormat) = "{{pod}} transmit"
  | (.panels[] | select(.id == 14).targets[0].expr) = "sum by (pod, container) (increase(kube_pod_container_status_restarts_total{namespace=\"streamforge\"}[$__range]))"
  | (.panels[] | select(.id == 14).targets[0].legendFormat) = "{{pod}} / {{container}}"
  | (.panels[] | select(.id == 14).title) = "Pod container restarts"
  | (.panels[] | select(.id == 14).description) = "Restart count from kube-state-metrics over the selected dashboard range."
' "$repository_root/grafana/dashboards/streamforge-overview.json" >"$dashboard_file"

kubectl create configmap prometheus-config \
  --namespace "$namespace" \
  --from-file=prometheus.yml="$manifest_dir/prometheus.yml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap grafana-datasource \
  --namespace "$namespace" \
  --from-file=prometheus.yml="$repository_root/grafana/provisioning/datasources/prometheus.yml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap grafana-dashboard-provider \
  --namespace "$namespace" \
  --from-file=streamforge.yml="$repository_root/grafana/provisioning/dashboards/streamforge.yml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap grafana-dashboards \
  --namespace "$namespace" \
  --from-file=streamforge-kubernetes.json="$dashboard_file" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$manifest_dir/resources.yaml"
kubectl rollout restart deployment/prometheus deployment/grafana \
  --namespace "$namespace"
kubectl rollout status deployment/kube-state-metrics \
  --namespace "$namespace" --timeout=180s
kubectl rollout status deployment/prometheus \
  --namespace "$namespace" --timeout=300s
kubectl rollout status deployment/grafana \
  --namespace "$namespace" --timeout=300s
kubectl get pods,services,persistentvolumeclaims --namespace "$namespace"
