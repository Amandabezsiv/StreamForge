# StreamForge

Distributed Media Processing Platform

Project changes are recorded in [CHANGELOG.md](CHANGELOG.md).

StreamForge is a backend engineering project focused on studying how systems behave as load, concurrency, processing cost, and infrastructure complexity increase.

## Local Development — Step 1

This first implementation step provides the FastAPI foundation, PostgreSQL,
the initial domain tables, local video upload, and a Python worker that uses
FFmpeg and ffprobe to produce the required v0.1 media outputs.

Prerequisites:

```text
Python 3.13+
uv
Docker with Docker Compose
```

Install the Python dependencies:

```bash
uv sync
```

Start PostgreSQL and worker:

```bash
docker compose up -d --build postgres worker
```

Apply the database migration:

```bash
uv run alembic upgrade head
```

Start the API:

```bash
uv run streamforge
```

Start the media worker in a second terminal (requires `ffmpeg` and `ffprobe`):

```bash
uv run streamforge-worker
```

Alternatively, run PostgreSQL and the worker together. The worker image already
contains FFmpeg:

```bash
docker compose up -d --build postgres worker
```

The API is then available at:

```text
API:     http://localhost:8000
Swagger: http://localhost:8000/docs
```

Run the automated tests:

```bash
uv run pytest
```

Run the first end-to-end benchmark:

```bash
docker compose up -d --build postgres api worker
uv run python scripts/benchmark_e2e.py --regenerate-fixture
```

The result is stored in
`experiments/001-single-worker-baseline/results.json`. See the experiment
README for the environment, fixture, metric definitions, and interpretation.

Endpoints available in this step:

```text
GET /health
POST /api/v1/videos
GET /api/v1/videos/{video_id}
GET /api/v1/videos/{video_id}/outputs
GET /api/v1/videos/{video_id}/jobs
```

Upload a video with curl:

```bash
curl -X POST http://localhost:8000/api/v1/videos \
  -F "file=@/path/to/video.mp4"
```

The accepted formats are `.mp4`, `.mov`, and `.mkv`. The default upload limit
is 1 GiB and can be changed with `MAX_UPLOAD_SIZE_BYTES` in `.env`.

After upload, the worker performs this sequence:

```text
claim PENDING job with a PostgreSQL row lock
  -> mark video and job PROCESSING
  -> extract duration, dimensions, codec, bitrate, and FPS with ffprobe
  -> generate thumbnail.jpg
  -> transcode 720p.mp4
  -> register both outputs
  -> mark job COMPLETED and video READY
```

If any processing command fails, the job and video become `FAILED`, and the
error is stored on the job and as a `JOB_FAILED` processing event.

Stop the database without deleting its data:

```bash
docker compose stop
```

The goal is not to build a YouTube clone or to add technologies simply because they are commonly associated with scalable systems.

The project is designed as an engineering laboratory for exploring:

- backend architecture;
- asynchronous processing;
- distributed systems;
- concurrency and parallelism;
- messaging;
- database performance;
- observability;
- fault tolerance;
- load testing;
- Kubernetes;
- CI/CD;
- system bottlenecks and scalability limits.

The project evolves progressively.

New technologies should, whenever possible, be introduced in response to an observed and measurable problem.

---

## Engineering Philosophy

StreamForge follows the cycle:

```text
Problem
   ↓
Measurement
   ↓
Hypothesis
   ↓
Solution
   ↓
Benchmark
   ↓
Comparison
   ↓
Conclusion
```

A technology is not introduced simply because it is popular or commonly used in production systems.

For example:

```text
Database latency increases under load
              ↓
        measure queries
              ↓
      identify bottleneck
              ↓
        test hypothesis
              ↓
         add caching
              ↓
       benchmark again
              ↓
      compare the results
```

A solution may also be removed if the measurements show that its operational complexity is not justified by the improvement it provides.

---

# Current Version

## StreamForge v0.1

The first version intentionally uses a minimal architecture.

Its purpose is to create a reproducible baseline before introducing distributed infrastructure.

```text
                       Client
                          │
                          │ HTTP
                          ▼
                       FastAPI
                     ┌────┴────┐
                     │         │
                     ▼         ▼
                PostgreSQL   Local Storage
                     │
                     │ pending jobs
                     ▼
                   Worker
                     │
                     ▼
               FFmpeg / ffprobe
```

The main question for this version is:

> How far can a simple architecture composed of FastAPI, PostgreSQL, local storage, and a single worker scale before its limitations become measurable?

---

# v0.1 Scope

The initial version supports:

- video upload;
- local storage of the original file;
- video registration in PostgreSQL;
- asynchronous processing outside the HTTP request;
- metadata extraction with `ffprobe`;
- thumbnail generation;
- 720p transcoding;
- processing status tracking;
- output registration;
- failure registration;
- manual processing retry;
- structured logs;
- processing timing measurements.

The following technologies are intentionally **not included yet**:

- Kafka;
- Redis;
- Kubernetes;
- S3;
- MinIO;
- Prometheus;
- Grafana;
- OpenTelemetry;
- autoscaling;
- Dead Letter Queues;
- distributed workers;
- automatic retries;
- canary deployments.

These capabilities will be introduced progressively when the system presents problems that justify them.

---

# Functional Requirements

## RF01 — Video Upload

The system must allow a client to upload a video.

Initially supported formats may include:

```text
.mp4
.mov
.mkv
```

The first implementation may impose an operational file size limit.

---

## RF02 — Original File Storage

The uploaded video must be stored outside PostgreSQL.

StreamForge v0.1 uses:

```text
Local filesystem
```

Future versions may migrate to:

```text
MinIO
↓
S3 or equivalent object storage
```

---

## RF03 — Video Registration

For every accepted upload, the system must create a persistent `Video` record.

---

## RF04 — Processing Job Creation

Every uploaded video must generate a `ProcessingJob`.

Processing must not occur inside the upload HTTP request.

---

## RF05 — Metadata Extraction

The worker must extract at least:

- duration;
- width;
- height;
- codec;
- bitrate;
- FPS.

The extraction should be performed using `ffprobe`.

---

## RF06 — Thumbnail Generation

The system must generate at least one thumbnail for every successfully processed video.

---

## RF07 — Video Transcoding

The initial version must generate one transcoded output:

```text
720p
```

Additional resolutions will be introduced later.

---

## RF08 — Processing Status

The system must expose the current processing state of a video.

---

## RF09 — Processing History

The system must retain information about processing attempts and relevant processing events.

---

## RF10 — Manual Retry

Failed jobs may be manually retried.

Automatic retries are intentionally postponed to later versions.

---

# Non-Functional Requirements

## Performance

Video processing must never block the HTTP upload request.

The system must distinguish between:

```text
Upload duration

API processing duration

Queue wait time

Media processing duration

Total time until video is ready
```

These measurements represent different parts of the system and must not be treated as a single latency metric.

---

## Durability

After the API confirms that an upload was accepted:

- the video file must exist in storage;
- the video record must exist in PostgreSQL;
- the corresponding processing job must exist.

Possible inconsistencies between storage and PostgreSQL will initially be documented rather than hidden.

---

## Observability

StreamForge v0.1 must produce structured logs containing useful identifiers whenever applicable:

```text
request_id
video_id
job_id
```

Important operations should also record their duration.

Examples:

```text
metadata extraction duration

thumbnail generation duration

transcoding duration

total processing duration
```

---

## Reproducibility

Performance experiments should use fixed datasets and documented infrastructure whenever possible.

A benchmark is only useful when its conditions can be reproduced.

---

# Domain Model

The initial domain contains four main entities:

```text
Video

ProcessingJob

VideoOutput

ProcessingEvent
```

---

# Video

A `Video` represents the media submitted by the user.

It does not represent a processing attempt.

Conceptually:

```text
Video

id
original_filename
storage_key
size_bytes

status

duration_seconds
width
height
fps
codec
bitrate

created_at
updated_at
```

The filename is never used as the identity of the video.

Each video receives a unique identifier.

---

# ProcessingJob

A `ProcessingJob` represents one processing attempt.

A single video may have multiple processing attempts.

```text
Video
 │
 ├── ProcessingJob attempt 1 → FAILED
 │
 ├── ProcessingJob attempt 2 → FAILED
 │
 └── ProcessingJob attempt 3 → COMPLETED
```

Conceptually:

```text
ProcessingJob

id
video_id

type
status
attempt

started_at
finished_at

error_code
error_message

created_at
updated_at
```

For StreamForge v0.1:

```text
type = PROCESS_VIDEO
```

The job currently performs:

```text
metadata extraction
        ↓
thumbnail generation
        ↓
720p transcoding
```

This job may be decomposed into smaller independent tasks in future versions.

---

# VideoOutput

A `VideoOutput` represents an artifact generated from a video.

Conceptually:

```text
VideoOutput

id
video_id

type
resolution

storage_key
size_bytes

created_at
```

Examples:

```text
type = THUMBNAIL
resolution = NULL
```

and:

```text
type = TRANSCODED_VIDEO
resolution = 720p
```

Future versions may contain:

```text
Thumbnail

360p

480p

720p

1080p

Preview

Audio

Subtitles
```

without requiring additional columns in the `videos` table.

---

# ProcessingEvent

A `ProcessingEvent` records important events that occurred during a processing attempt.

Conceptually:

```text
ProcessingEvent

id
video_id
job_id

event_type
message

created_at
```

Possible events:

```text
JOB_CREATED

JOB_STARTED

METADATA_EXTRACTED

THUMBNAIL_CREATED

TRANSCODING_STARTED

TRANSCODING_COMPLETED

JOB_COMPLETED

JOB_FAILED
```

`ProcessingEvent` is initially an operational history mechanism.

It is **not Event Sourcing** and is not the source of truth for the application state.

---

# Video State Machine

The initial video states are:

```text
UPLOADED

PROCESSING

READY

FAILED
```

State transitions:

```text
                 ┌─────────────► FAILED
                 │
UPLOADED ─────► PROCESSING ─────► READY
```

A `Video` describes the overall state of the media.

---

# Processing Job State Machine

The initial processing job states are:

```text
PENDING

PROCESSING

COMPLETED

FAILED
```

State transitions:

```text
PENDING
   │
   ▼
PROCESSING
   │
   ├────────────► COMPLETED
   │
   └────────────► FAILED
```

States such as:

```text
RETRYING
TIMED_OUT
CANCELLED
DEAD_LETTERED
```

will only be introduced when the system actually implements the corresponding behavior.

---

# Initial Invariants

The following invariants must hold independently of implementation details.

## I01 — Job ownership by video

Every `ProcessingJob` must belong to exactly one `Video`.

---

## I02 — Output ownership

A `VideoOutput` cannot exist without a corresponding `Video`.

---

## I03 — Completed job timestamps

A job with:

```text
status = COMPLETED
```

must have a valid `finished_at`.

---

## I04 — Processing timestamps

A job with:

```text
status = PROCESSING
```

must have a valid `started_at`.

---

## I05 — Failed jobs

A failed job must contain enough information to determine that a failure occurred and help identify its cause.

---

## I06 — Video readiness

A video may only transition to:

```text
READY
```

when every output required by the current version has been successfully generated.

For v0.1 this means:

```text
metadata

thumbnail

720p output
```

---

## I07 — Job ownership by worker

At most one worker should actively process a given `ProcessingJob` at the same time.

The mechanism used to guarantee this invariant may evolve.

Possible future implementations include PostgreSQL row locks, leases, message brokers, or other coordination mechanisms.

---

# Storage Layout

The original filename is not used as the storage identity.

An initial directory layout may look like:

```text
storage/
└── videos/
    └── <video_uuid>/
        ├── original.mp4
        ├── thumbnail.jpg
        └── 720p.mp4
```

Example:

```text
storage/
└── videos/
    └── 7c9f72e4-.../
        ├── original.mp4
        ├── thumbnail.jpg
        └── 720p.mp4
```

This structure should make future migration to object storage easier.

---

# Upload Flow

StreamForge v0.1 accepts uploads through the API.

```text
Client
   │
   │ POST /api/v1/videos
   ▼
FastAPI
   │
   ├── validate request
   │
   ├── generate video UUID
   │
   ├── persist original file
   │
   ├── create Video
   │
   ├── create ProcessingJob(PENDING)
   │
   ▼
202 Accepted
```

Example response:

```json
{
  "video_id": "7c9f72e4-...",
  "status": "UPLOADED"
}
```

The HTTP request must not wait for FFmpeg processing to finish.

---

# Processing Flow

The initial worker uses PostgreSQL to discover pending work.

Conceptually:

```text
Worker
   │
   ├── search for PENDING job
   │
   ├── acquire job
   │
   ├── set PROCESSING
   │
   ├── run ffprobe
   │
   ├── persist metadata
   │
   ├── generate thumbnail
   │
   ├── register thumbnail output
   │
   ├── transcode 720p
   │
   ├── register video output
   │
   ├── set job COMPLETED
   │
   └── set video READY
```

If any required stage fails:

```text
ProcessingJob → FAILED

Video → FAILED

ProcessingEvent → JOB_FAILED
```

The exact failure strategy may evolve in future versions.

---

# Initial Job Queue

StreamForge v0.1 intentionally uses PostgreSQL as its job queue.

The initial worker will conceptually search for work using a query similar to:

```sql
SELECT *
FROM processing_jobs
WHERE status = 'PENDING'
ORDER BY created_at
LIMIT 1;
```

This design is expected to expose limitations.

Potential problems include:

- polling overhead;
- race conditions;
- concurrent workers acquiring the same job;
- database contention;
- job acquisition latency;
- stale `PROCESSING` jobs;
- pressure on PostgreSQL.

These limitations are intentional learning opportunities.

Future experiments may introduce:

```text
SELECT ... FOR UPDATE

SKIP LOCKED

leases

heartbeats

Kafka
```

Kafka should only be introduced after the limitations of the current architecture can be demonstrated.

---

# Consistency Model

StreamForge cannot create a single ACID transaction covering:

```text
PostgreSQL

filesystem

FFmpeg
```

For example:

```text
thumbnail generated
        ↓
database insert starts
        ↓
PostgreSQL fails
```

The file may exist while PostgreSQL has no corresponding record.

The first version will document these inconsistencies rather than introduce complex distributed coordination prematurely.

Future versions may investigate:

- idempotency;
- reconciliation;
- compensating actions;
- eventual consistency.

---

# Initial API

The first API surface should remain small.

## Upload video

```http
POST /api/v1/videos
```

Expected response:

```http
202 Accepted
```

---

## Get video

```http
GET /api/v1/videos/{video_id}
```

Returns information such as:

- status;
- metadata;
- timestamps;
- processing information.

---

## Get outputs

```http
GET /api/v1/videos/{video_id}/outputs
```

---

## Get processing jobs

```http
GET /api/v1/videos/{video_id}/jobs
```

---

## Retry failed job

```http
POST /api/v1/jobs/{job_id}/retry
```

---

## Health check

```http
GET /health
```

Future Kubernetes versions may introduce:

```text
/health/live

/health/ready
```

---

# Initial Repository Structure

```text
streamforge/
│
├── src/
│   └── streamforge/
│       │
│       ├── api/
│       │   ├── routes/
│       │   ├── dependencies.py
│       │   └── app.py
│       │
│       ├── core/
│       │   ├── config.py
│       │   ├── logging.py
│       │   └── exceptions.py
│       │
│       ├── models/
│       │   ├── video.py
│       │   ├── processing_job.py
│       │   ├── video_output.py
│       │   └── processing_event.py
│       │
│       ├── schemas/
│       │
│       ├── repositories/
│       │
│       ├── services/
│       │
│       ├── storage/
│       │
│       ├── media/
│       │   ├── ffmpeg.py
│       │   └── ffprobe.py
│       │
│       └── workers/
│           └── processor.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── performance/
│
├── load-tests/
│   ├── scenarios/
│   └── fixtures/
│
├── experiments/
│
├── docs/
│   ├── architecture/
│   └── adr/
│
├── scripts/
├── migrations/
│
├── .github/
│   └── workflows/
│
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

The project starts as a modular monolith with separate API and worker processes.

Microservices will not be introduced unless there is a concrete architectural reason.

---

# API Testing Strategy

Different tools serve different purposes.

## OpenAPI / Swagger

FastAPI OpenAPI documentation is used for:

- API contract documentation;
- quick endpoint exploration;
- schema inspection.

---

## Postman

Postman may be used for:

- manual API exploration;
- reusable request collections;
- environment variables;
- multi-step request flows.

Example flow:

```text
Upload video
     ↓
capture video_id
     ↓
check processing status
     ↓
retrieve outputs
```

The OpenAPI schema remains the API contract source of truth.

---

## Automated Tests

Regression tests should primarily use:

```text
pytest

HTTPX / FastAPI TestClient
```

Manual Postman tests do not replace automated tests.

---

# Load Testing

StreamForge uses **k6** as its primary HTTP load generation tool.

Load testing will be separated into different workloads.

## API Load

Examples:

```text
GET /videos/{id}

GET /videos/{id}/jobs

GET /videos/{id}/outputs
```

Measures API behavior independently from media processing.

---

## Upload Load

Multiple clients upload real video files concurrently.

Possible concurrency levels:

```text
1

10

50

100

500
```

These levels will only be used progressively.

---

## Processing Load

Measures worker throughput independently from API request throughput.

Important metrics include:

```text
videos / minute

queue wait time

processing duration

CPU

memory

disk I/O
```

---

## End-to-End Load

Measures:

```text
upload
   ↓
job created
   ↓
job acquired
   ↓
media processed
   ↓
video READY
```

API throughput and processing throughput must never be treated as the same metric.

A system may handle thousands of lightweight HTTP requests while processing only a small number of videos per minute.

---

# Performance Metrics

Relevant metrics include:

## HTTP

```text
requests per second

p50

p95

p99

error rate
```

## Processing

```text
queue_wait_time

metadata_duration

thumbnail_duration

transcoding_duration

processing_duration

total_time_to_ready

videos_per_minute
```

## Infrastructure

```text
CPU

memory

disk reads

disk writes

network I/O
```

## PostgreSQL

```text
active connections

query duration

locks

transactions

connection pool usage
```

Future versions may additionally track:

```text
Kafka consumer lag

partition throughput

retry count

DLQ size
```

---

# Benchmark Dataset

Performance comparisons should use fixed media fixtures.

Example dataset:

```text
small.mp4
≈ 10 MB
≈ 30 seconds

medium.mp4
≈ 100 MB
≈ 5 minutes

large.mp4
≈ 500 MB
≈ 20 minutes
```

Large binary fixtures should not necessarily be committed to Git.

Scripts may generate deterministic test videos using FFmpeg.

---

# Engineering Experiments

Every important performance or architecture investigation should be documented.

Structure:

```text
experiments/
│
├── 001-single-worker-baseline/
│   ├── README.md
│   └── results.csv
│
├── 002-multiple-workers/
│
├── 003-postgres-job-locking/
│
└── ...
```

Each experiment should document whenever possible:

```text
Context

Question

Hypothesis

Architecture

Infrastructure

Configuration

Dataset

Load

Metrics

Results

Bottleneck

Change

New benchmark

Conclusion
```

An experiment is allowed to conclude that an optimization did not improve the system.

That is still a valid engineering result.

---

# Experiment 001 — Single Worker Baseline

The first benchmark establishes the control architecture.

```text
FastAPI

PostgreSQL

local filesystem

1 worker

FFmpeg
```

Initial workload:

```text
10 videos
```

The first objective is not to maximize throughput.

The objective is to understand where time and resources are being consumed.

Measurements should include:

```text
upload duration

queue wait time

metadata extraction duration

thumbnail duration

transcoding duration

total processing duration

CPU

memory

disk I/O

errors
```

This experiment becomes the baseline for future comparisons.

---

# Architecture Decision Records

Important architecture decisions should be documented using ADRs.

Directory:

```text
docs/adr/
```

Example:

```text
ADR-001-use-postgresql-as-initial-job-queue.md
```

Recommended format:

```text
# ADR-001: PostgreSQL as initial job queue

Status: Accepted

## Context

Why does this decision exist?

## Decision

What was chosen?

## Alternatives Considered

What alternatives were evaluated?

## Consequences

What benefits and limitations does this introduce?

## Revisit When

Which measurable conditions would justify revisiting this decision?
```

Architecture decisions may later be superseded.

Example:

```text
ADR-001
PostgreSQL job queue

        ↓ superseded by

ADR-008
Kafka job distribution
```

The ADR history should show how the architecture evolved and why.

---

# Git Strategy

StreamForge uses a simplified trunk-based development workflow.

The permanent branch is:

```text
main
```

Development happens through short-lived branches.

Examples:

```text
feat/video-upload

feat/local-storage

feat/processing-worker

fix/job-status-transition

perf/postgres-job-polling

experiment/multiple-workers

docs/postgres-queue-adr
```

Branches should be merged and removed rather than kept indefinitely.

A permanent `develop` branch is intentionally not used.

---

# Branch Naming

Preferred prefixes:

```text
feat/

fix/

refactor/

perf/

test/

docs/

chore/

experiment/
```

---

# Commit Convention

The project follows Conventional Commits where practical.

Examples:

```text
feat: add video upload endpoint

feat: add processing job model

fix: prevent duplicate job acquisition

perf: reduce worker polling frequency

test: add processing integration tests

docs: add postgres queue ADR

refactor: isolate ffmpeg adapter
```

---

# Pull Requests

Even though StreamForge may initially have a single developer, changes should preferably reach `main` through Pull Requests.

PRs should answer:

```text
What changed?

Why?

How was it tested?

Are there architectural implications?

Were performance metrics affected?
```

For performance-related PRs, before/after measurements should be included whenever possible.

Example:

```text
Before

PostgreSQL queries: 120 qps
CPU: 18%

After

PostgreSQL queries: 25 qps
CPU: 9%
```

---

# CI

GitHub Actions should be introduced early.

Initial CI:

```text
Pull Request
      │
      ├── formatting
      ├── lint
      ├── type checking
      ├── unit tests
      └── integration tests
```

Possible tooling:

```text
Ruff

Pyright or mypy

pytest
```

Future CI stages may include:

```text
Docker image build

security scanning

coverage

staging deployment

smoke tests

production deployment
```

---

# Main Branch Policy

The `main` branch should remain:

```text
buildable

tested

runnable

documented
```

Direct pushes to `main` should preferably be disabled.

Pull Requests and successful CI checks should be required before merging.

---

# Versioning

Important milestones may be tagged.

Example:

```text
v0.1.0
```

for the first complete pipeline:

```text
upload
   ↓
job
   ↓
metadata
   ↓
thumbnail
   ↓
720p
   ↓
READY
```

Versions should represent meaningful architectural or functional milestones rather than arbitrary progress.

---

# Technical Roadmap

The roadmap is intentionally problem-driven.

## Phase 0 — Foundations

```text
FastAPI

PostgreSQL

SQLAlchemy

Alembic

Docker Compose

FFmpeg

pytest

structured logging
```

---

## Phase 1 — Single Worker

Build the first complete processing pipeline and establish the baseline.

---

## Phase 2 — Worker Concurrency

Compare:

```text
1 worker

2 workers

4 workers

8 workers
```

Observe when throughput stops increasing and identify the next bottleneck.

---

## Phase 3 — PostgreSQL Job Coordination

Investigate:

```text
race conditions

SELECT FOR UPDATE

SKIP LOCKED

job ownership

stale jobs

leases

worker crashes
```

---

## Phase 4 — Messaging

Introduce Kafka only if measurements justify moving job distribution away from PostgreSQL.

Study:

```text
producers

consumers

consumer groups

partitions

offsets

delivery semantics

consumer lag
```

---

## Phase 5 — Job Decomposition

Evolve:

```text
PROCESS_VIDEO
```

into independent tasks such as:

```text
EXTRACT_METADATA

GENERATE_THUMBNAIL

TRANSCODE_360P

TRANSCODE_480P

TRANSCODE_720P

TRANSCODE_1080P
```

Study fan-out and coordination.

---

## Phase 6 — Failure Engineering

Reproduce:

```text
worker crash

duplicate message

Kafka unavailable

PostgreSQL unavailable

slow storage

timeouts

stuck jobs
```

Then investigate:

```text
idempotency

retries

exponential backoff

DLQ

graceful shutdown

eventual consistency
```

---

## Phase 7 — Load Testing

Progressively test workloads such as:

```text
10 videos

50 videos

100 videos

500 videos
```

Identify system saturation points.

---

## Phase 8 — PostgreSQL Performance

Investigate:

```text
slow queries

indexes

EXPLAIN ANALYZE

locks

connection pooling

pool exhaustion
```

---

## Phase 9 — Cache

Introduce Redis only after identifying a measurable read-latency or database-load problem that caching could reasonably address.

Measure before and after.

---

## Phase 10 — Observability

Introduce:

```text
Prometheus

Grafana

OpenTelemetry

distributed tracing
```

---

## Phase 11 — Kubernetes

Deploy independent API and worker workloads.

Study:

```text
resource requests

resource limits

readiness probes

liveness probes

graceful shutdown
```

---

## Phase 12 — Autoscaling

Compare scaling signals such as:

```text
CPU

queue size

Kafka consumer lag
```

Investigate when increasing worker count no longer improves throughput.

---

## Phase 13 — CI/CD

Evolve toward:

```text
lint
↓
tests
↓
security scan
↓
image build
↓
staging
↓
smoke tests
↓
production
```

Later investigate:

```text
canary deployment

blue/green deployment

rollback
```

---

## Phase 14 — Resilience Experiments

Intentionally introduce failures and document system behavior.

Examples:

```text
kill worker during transcoding

duplicate Kafka messages

increase storage latency

saturate CPU

restrict memory

exhaust PostgreSQL connections

increase consumer lag
```

---

# Known Limitations — v0.1

The first version intentionally contains limitations.

Examples:

- PostgreSQL polling is used as a job queue;
- only one worker is initially used;
- storage is local;
- there is no automatic retry;
- there is no distributed coordination;
- storage and database operations are not part of one atomic transaction;
- no cache exists;
- no message broker exists;
- no Kubernetes infrastructure exists;
- observability is initially limited to structured logs and timings.

These limitations are not hidden.

They define the starting point for future engineering experiments.

---

# Core Principle

> **No technology is introduced without a reason.**

StreamForge should demonstrate not only which technologies were used, but why they became necessary, what problem they attempted to solve, and whether measurements showed that they actually improved the system.

The final goal is to understand not only how to build a distributed system, but how and why a simpler system gradually becomes one.
