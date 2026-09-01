# StreamForge

Distributed video-processing backend and engineering laboratory.

StreamForge accepts video uploads, stores durable job state in PostgreSQL, and
uses independent workers with FFmpeg/ffprobe to extract metadata, generate a
thumbnail, and transcode a 720p output.

```text
Released:     v0.1.0 — Local Distributed Baseline
Next:         v0.2.x — Orchestration and Elastic Scaling
Runtime now:  Docker Compose
```

## Contents

- [What is implemented](#what-is-implemented)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [API](#api)
- [Processing and reliability](#processing-and-reliability)
- [Observability](#observability)
- [Tests and benchmarks](#tests-and-benchmarks)
- [Project evolution](#project-evolution)
- [Known limitations](#known-limitations)
- [Documentation](#documentation)

## What is implemented

StreamForge v0.1.0 is the completed local distributed baseline:

- streaming uploads for `.mp4`, `.mov`, and `.mkv`;
- original files stored on the local filesystem;
- videos, processing jobs, events, and outputs stored in PostgreSQL;
- atomic job acquisition with `SELECT FOR UPDATE SKIP LOCKED`;
- PostgreSQL `LISTEN/NOTIFY` pickup with polling fallback;
- multiple independent worker containers;
- metadata extraction with `ffprobe`;
- thumbnail generation and H.264/AAC 720p transcoding with FFmpeg;
- atomic temporary-file publication before output registration;
- worker leases, renewal, abandoned-job detection, and recovery;
- Prometheus application metrics, Grafana, and cAdvisor container metrics;
- reproducible reliability and performance experiments.

## Architecture

```text
                              ┌──────────────┐
Client ───── HTTP ───────────▶│   FastAPI    │
                              └──────┬───────┘
                                     │
                         ┌───────────┴───────────┐
                         ▼                       ▼
                  ┌────────────┐          ┌─────────────┐
                  │ PostgreSQL │          │ Local files │
                  │ jobs/state │          │ videos      │
                  └─────┬──────┘          └──────┬──────┘
                        │                         │
                        │ claim + notify          │ shared mount
                        ▼                         ▼
                 ┌─────────────────────────────────────┐
                 │ Worker containers                   │
                 │ ffprobe → thumbnail → 720p FFmpeg   │
                 └─────────────────────────────────────┘

Prometheus ◀── API metrics + worker metrics + cAdvisor
     │
     └──────────────────────────────▶ Grafana
```

PostgreSQL is both the application database and durable job queue. Local media
storage is mounted into the API and every worker container.

## Quick start

Requirements: Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and Docker with
Docker Compose.

```bash
uv sync
cp .env.example .env
docker compose up -d --build --scale worker=4
```

The API container applies Alembic migrations before starting. Check the stack:

```bash
docker compose ps
curl http://localhost:8000/health
```

| Component | URL |
| --- | --- |
| API health | <http://localhost:8000/health> |
| Swagger UI | <http://localhost:8000/docs> |
| API metrics | <http://localhost:8000/metrics> |
| Prometheus | <http://localhost:9090> |
| Grafana | <http://localhost:3000/d/streamforge-overview/streamforge-overview> |

Stop containers without deleting persistent volumes:

```bash
docker compose stop
```

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | API and database health |
| `GET` | `/metrics` | Prometheus exposition |
| `POST` | `/api/v1/videos` | Upload and enqueue a video |
| `GET` | `/api/v1/videos/{video_id}` | Video state and metadata |
| `GET` | `/api/v1/videos/{video_id}/outputs` | Registered outputs |
| `GET` | `/api/v1/videos/{video_id}/jobs` | Processing attempts |

Upload a video:

```bash
curl -X POST http://localhost:8000/api/v1/videos \
  -F "file=@/path/to/video.mp4"
```

Uploads are streamed to disk and limited by `MAX_UPLOAD_SIZE_BYTES` (1 GiB by
default). Processing does not run inside the upload request.

## Processing and reliability

```text
upload original
      ↓
register Video + PENDING ProcessingJob
      ↓
NOTIFY worker (polling remains as fallback)
      ↓
atomic claim + renewable ownership lease
      ↓
ffprobe metadata extraction
      ↓
thumbnail.tmp → atomic rename → thumbnail.jpg
      ↓
720p.tmp → atomic rename → 720p.mp4
      ↓
register outputs → job COMPLETED → video READY
```

If a worker dies, its lease expires. Another worker records the abandoned
attempt as failed, cleans temporary artifacts, and creates a new pending
attempt. Final artifacts are registered only after atomic publication.

## Observability

Prometheus collects three metric groups:

| Source | Examples |
| --- | --- |
| API | `streamforge_jobs_pending`, `streamforge_jobs_processing` |
| Workers | outcomes, pickup/processing histograms, lease expirations, retries |
| cAdvisor | container CPU, CFS throttling, memory, network, and start time |

The provisioned Grafana dashboard contains job, latency, throughput, lease,
retry, and container-resource panels. Container restart visibility is inferred
from `container_start_time_seconds`; it is not a durable restart audit.

See [ADR-002](docs/adr/ADR-002-prometheus-observability.md) for metric semantics
and security considerations. cAdvisor has privileged host access for this local
laboratory and must not be exposed publicly.

## Tests and benchmarks

```bash
uv run ruff check .
uv run black --check .
uv run pytest
```

The `experiments/` directory contains reproducible studies of:

- baseline latency and fixture-size effects;
- worker and FFmpeg thread scaling;
- job acquisition and duplicate prevention;
- worker, database, notification, and publication failures;
- PostgreSQL queue and idle-polling costs;
- LISTEN/NOTIFY latency and fallback recovery;
- observability, high load, sustained arrivals, and CPU limits.

Each experiment provides its command, raw result, and conclusion. Start with
[Experiment 001](experiments/001-single-worker-baseline/README.md), then see
[Experiment 021](experiments/021-worker-cpu-limits/README.md) for the latest
capacity comparison.

## Project evolution

```text
v0.1.0 — LOCAL DISTRIBUTED BASELINE ✅
│
├── reliability
├── PostgreSQL queue + LISTEN/NOTIFY
├── benchmarks
└── Prometheus + Grafana + container metrics
          │
          ▼
v0.2.x — KUBERNETES (planned)
│
├── local Kubernetes cluster
├── API and worker Deployments
├── Services, ConfigMaps, and Secrets
├── resource requests and limits
├── readiness and liveness probes
├── pod-failure experiments
└── HPA
     ├── CPU-based
     └── queue-based
```

The first v0.2 milestone reproduces v0.1 behavior in a local Kind cluster before
changing application behavior. See
[Experiment 022](experiments/022-kubernetes-local-deployment/README.md) for the
deployment and parity validation. HPA is not implemented yet.

The main architectural question is shared media storage: API and worker pods
must see the same files. A local PersistentVolume can establish single-node
parity; multi-node operation will later require suitable shared or object
storage.

## Known limitations

- PostgreSQL serves as both database and job queue.
- Local filesystem storage requires a shared mount.
- Scaling is manual through Docker Compose.
- Automatic retries are intentionally absent; only abandoned leases recover.
- Failed jobs do not yet have a public manual-retry endpoint.
- Storage publication and database commit cannot form one atomic transaction.
- Metrics endpoints have no authentication in the local environment.
- HPA, object storage, and multi-node Kubernetes operation are not implemented.

## Documentation

- [Changelog](CHANGELOG.md)
- [Detailed v0.1 study reference](docs/v0.1-study-reference.md)
- [Architecture decisions](docs/adr/)
- [Experiments](experiments/)
- [CI workflow](.github/workflows/ci.yml)

StreamForge follows this measurement-driven cycle:

```text
problem → measurement → hypothesis → solution → benchmark → comparison
```

Technologies are introduced only when an observed problem justifies their
operational cost.
