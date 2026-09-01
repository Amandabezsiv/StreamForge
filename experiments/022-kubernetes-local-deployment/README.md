# Experiment 022: Kubernetes Local Deployment

## Question

Can the existing StreamForge v0.1 architecture run on a local Kubernetes
cluster without changing application behavior?

## Scope

This first v0.2 experiment changes orchestration, not the processing design:

- one single-node Kind cluster;
- one FastAPI Deployment and ClusterIP Service;
- one PostgreSQL StatefulSet and Service;
- four worker replicas and a headless metrics Service;
- a Kubernetes Job for Alembic migrations;
- ConfigMap for non-sensitive settings;
- Secret for local database credentials and `DATABASE_URL`;
- one shared media PersistentVolumeClaim;
- readiness and liveness probes;
- resource requests and limits.

The API contract, PostgreSQL queue, `LISTEN/NOTIFY`, atomic claims, worker
leases, ffprobe metadata, thumbnail generation, 720p transcode, atomic output
publication, and database records remain unchanged.

Prometheus, Grafana, cAdvisor, HPA, ingress, and pod-failure injection are not
part of this experiment. They will be added only after application parity is
demonstrated.

## Storage constraint

Kind uses one node. Its default local provisioner can satisfy the
`ReadWriteOnce` media claim and every StreamForge pod is scheduled on that same
node, allowing the API and workers to see identical files.

This does **not** prove multi-node storage correctness. A future multi-node
deployment needs a `ReadWriteMany` filesystem or object storage such as S3.

## Deploy

Install Docker, `kind`, and `kubectl`, then run:

```bash
./kubernetes/local/deploy.sh
```

The script creates the cluster when necessary, builds and loads
`streamforge:local`, deploys PostgreSQL, runs migrations, starts the API and
four workers, waits for their rollouts, and prints the resulting resources.

## Validate end to end

```bash
uv run python scripts/benchmark_kubernetes_local.py
```

The validation opens a temporary API port-forward, uploads the deterministic
small fixture, waits for a terminal state, and verifies:

- video becomes `READY`;
- duration, dimensions, FPS, codec, and bitrate exist;
- non-empty thumbnail is registered;
- non-empty 720p transcode is registered;
- processing job becomes `COMPLETED`;
- Kubernetes pod placement and readiness are recorded.

Raw output is written to [results.json](results.json).

## Inspect

```bash
kubectl get all,pvc -n streamforge
kubectl logs -n streamforge deployment/streamforge-api
kubectl logs -n streamforge deployment/streamforge-worker --all-pods=true
kubectl port-forward -n streamforge service/streamforge-api 8000:8000
```

## Cleanup

The cluster and both persistent claims are removed with:

```bash
kind delete cluster --name streamforge
```

## Result

Recorded on 2026-09-01 with Kind 0.33.0 and Kubernetes 1.37.0. The local
deployment completed successfully.

### Kubernetes resources

| Resource | Desired | Result |
| --- | ---: | --- |
| PostgreSQL StatefulSet | 1 pod | Ready, 0 restarts |
| Migration Job | 1 completion | Succeeded, 0 restarts |
| FastAPI Deployment | 1 pod | Ready, 0 restarts |
| Worker Deployment | 4 pods | All ready, 0 restarts |
| PostgreSQL PVC | 2 GiB | Bound, `ReadWriteOnce` |
| Shared media PVC | 5 GiB | Bound, `ReadWriteOnce` |

All pods ran on `streamforge-control-plane`, which satisfies the single-node
shared-volume assumption.

### End-to-end processing

| Metric | Result |
| --- | ---: |
| Fixture | `baseline-small.mp4` |
| Input | 10 s, 640×360, H.264, 1,340,597 bytes |
| Final video state | `READY` |
| Queue wait | 0.011 s |
| Processing duration | 6.632 s |
| Total time to ready | 6.655 s |
| Thumbnail | Registered, 17,956 bytes |
| 720p output | Registered, 2,157,435 bytes |
| Errors | 0 |

The API returned duration, width, height, FPS, codec, and bitrate. The 720p
artifact was independently inspected from a worker pod with ffprobe and
contained a 1280×720 H.264 video stream and AAC audio stream.

Both the thumbnail and transcode created through the API/worker pipeline were
readable from the API pod and a worker pod. This validates the local shared-PVC
behavior required by the unchanged filesystem storage design.

### Conclusion

The v0.1 application behavior runs successfully under local Kubernetes
orchestration without application-code changes. Kubernetes now owns pod
creation, service discovery, rollout readiness, restart policy, configuration,
secrets, resource boundaries, and persistent-volume attachment.

This is orchestration parity, not elasticity proof. The next experiments should
kill a processing worker pod and verify lease recovery, then compare manual pod
scaling before introducing HPA.
