#!/usr/bin/env bash
set -euo pipefail

cluster_name=${KIND_CLUSTER_NAME:-streamforge}
namespace=streamforge
manifest_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$manifest_dir/../.." && pwd)

for command_name in docker kind kubectl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 1
  fi
done

if ! kind get clusters | grep -Fxq "$cluster_name"; then
  kind create cluster --name "$cluster_name" --config "$manifest_dir/kind-config.yaml"
fi

docker build -t streamforge:local "$repository_root"
kind load docker-image streamforge:local --name "$cluster_name"

kubectl apply -f "$manifest_dir/namespace.yaml"
kubectl apply -f "$manifest_dir/configmap.yaml"
kubectl apply -f "$manifest_dir/secret.yaml"
kubectl apply -f "$manifest_dir/storage.yaml"
kubectl apply -f "$manifest_dir/postgres.yaml"
kubectl rollout status statefulset/postgres -n "$namespace" --timeout=180s

kubectl delete job streamforge-migrate -n "$namespace" --ignore-not-found
kubectl apply -f "$manifest_dir/migration-job.yaml"
kubectl wait --for=condition=complete job/streamforge-migrate \
  -n "$namespace" --timeout=180s

kubectl apply -f "$manifest_dir/api.yaml"
kubectl apply -f "$manifest_dir/worker.yaml"
kubectl rollout status deployment/streamforge-api -n "$namespace" --timeout=180s
kubectl rollout status deployment/streamforge-worker -n "$namespace" --timeout=300s

kubectl get pods,services,persistentvolumeclaims -n "$namespace"
