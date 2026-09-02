# Changelog

All notable changes to StreamForge are documented in this file.

The project follows progressive, experiment-driven development. Changelog
entries describe completed capabilities; benchmark results and architectural
conclusions belong in `experiments/` and `docs/adr/`.

## [Unreleased]

### Changed

- Declared v0.1.0 complete as the local distributed baseline and documented the
  planned v0.2.x Kubernetes orchestration and elastic-scaling direction.
- Reorganized the main README as a concise operational overview and moved the
  detailed v0.1 specification and study notes into `docs/v0.1-study-reference.md`.

### Added

- Experiment 022 Kind deployment manifests and end-to-end Kubernetes parity
  validation for the API, PostgreSQL, shared media storage, migrations, and four
  worker pods.
- Experiment 023 measurement of Kubernetes worker-Pod replacement and
  application lease recovery during FFmpeg transcoding.

## [0.1.0] - 2026-09-01

### Added

- Persistent processing benchmark measurements on each job
- Reproducible upload-to-ready benchmark runner and deterministic video fixture
- Experiment 001 single-worker baseline result
- Small, medium, and large deterministic benchmark fixture profiles
- Repeated benchmark runner with mean, median, min, max, p50, and p95 statistics
- Experiment 002 concurrent-upload queue-growth benchmark
- Experiment 003 multi-worker throughput and resource benchmark
- Experiment 004 three- and four-worker scaling comparison
- Configurable FFmpeg threads and Experiment 005 thread-allocation matrix
- Experiment 006 atomic PostgreSQL job-claim contention benchmark
- Experiment 007 worker-crash database and filesystem consistency inspection

- FastAPI application with OpenAPI and Swagger documentation
- PostgreSQL 17 development database through Docker Compose
- SQLAlchemy models for videos, processing jobs, outputs, and events
- Alembic migration for the initial domain schema
- Local filesystem storage under `storage/videos/<video_id>/`
- Streaming video upload endpoint with a configurable size limit
- Validation for `.mp4`, `.mov`, and `.mkv` uploads
- Automatic creation of a pending processing job after each accepted upload
- Python worker using PostgreSQL as its initial job queue
- Atomic job acquisition using `FOR UPDATE SKIP LOCKED`
- Video metadata extraction with ffprobe:
  - duration
  - width and height
  - codec
  - bitrate
  - FPS
- Thumbnail generation with FFmpeg
- H.264/AAC 720p transcoding with FFmpeg
- Registration of thumbnail and transcoded-video outputs
- Video status tracking through `UPLOADED`, `PROCESSING`, `READY`, and `FAILED`
- Job status tracking through `PENDING`, `PROCESSING`, `COMPLETED`, and `FAILED`
- Processing events for important worker stages and failures
- Failure details stored on processing jobs
- Docker worker image containing FFmpeg and ffprobe
- Automated tests covering the API upload flow and worker processing flow
- Atomic publication of FFmpeg outputs through same-directory temporary files,
  preventing partial thumbnails or transcodes from appearing at final paths.
- Experiment 008 crash verification for atomic FFmpeg output publication.
- Worker processing leases with ownership checks, heartbeat renewal, abandoned
  attempt recovery, idempotent retry output registration, and temporary cleanup.
- Experiment 009 worker lease crash-recovery verification.
- Experiments 010 and 011 for database loss during renewal and a crash between
  atomic output publication and database commit.
- Experiment 012 PostgreSQL queue acquisition concurrency and saturation sweep.
- Experiment 013 PostgreSQL and worker overhead from polling an empty queue.
- Experiment 014 job pickup latency versus PostgreSQL idle-polling cost.
- PostgreSQL `LISTEN/NOTIFY` worker wake-ups with polling fallback.
- Experiment 015 `LISTEN/NOTIFY` versus polling latency and database cost.
- Experiment 016 recovery after a notification is missed during listener loss.
- Prometheus API and worker instrumentation, Docker DNS worker discovery,
  Compose deployment, and ADR-002 observability metric semantics.
- Experiment 017 end-to-end Prometheus target, series, and live-job validation.
- Provisioned Grafana 13.2.0 Prometheus datasource and nine-panel StreamForge
  dashboard, validated in Experiment 018.
- Experiment 019 observed end-to-end high load with four workers and 50 medium
  videos.
- Experiment 020 sustained arrival-rate sweep against four-worker processing
  capacity.
- Experiment 021 four-worker sustained-load comparison with a one-CPU limit per
  worker container.
- cAdvisor runtime metrics scraped by Prometheus and Grafana panels for
  container CPU, CFS throttling, memory, network, and inferred restarts.

### API

- `GET /health`
- `POST /api/v1/videos`
- `GET /api/v1/videos/{video_id}`
- `GET /api/v1/videos/{video_id}/outputs`
- `GET /api/v1/videos/{video_id}/jobs`

### Known limitations

- Processing uses one logical job containing every media-processing stage
- PostgreSQL is polled for pending jobs
- Local storage is shared directly between the API and worker
- Failed jobs cannot yet be retried through the API
- Processing events do not yet have a public API endpoint
- Upload validation initially relies on the filename extension; ffprobe detects
  invalid media during worker processing
- Automatic retries are intentionally not implemented
- Media processing and HTTP API capacity have not yet been benchmarked

[Unreleased]: https://github.com/Amandabezsiv/StreamForge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Amandabezsiv/StreamForge/releases/tag/v0.1.0
