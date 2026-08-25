# Changelog

All notable changes to StreamForge are documented in this file.

The project follows progressive, experiment-driven development. Changelog
entries describe completed capabilities; benchmark results and architectural
conclusions belong in `experiments/` and `docs/adr/`.

## [Unreleased]

### Planned

- Manual retry endpoint for failed processing jobs
- Structured request and processing logs
- Processing-duration measurements
- Initial API and worker capacity benchmarks

## [0.1.0] - 2026-08-23

### Added

- Persistent processing benchmark measurements on each job
- Reproducible upload-to-ready benchmark runner and deterministic video fixture
- Experiment 001 single-worker baseline result
- Small, medium, and large deterministic benchmark fixture profiles
- Repeated benchmark runner with mean, median, min, max, p50, and p95 statistics
- Experiment 002 concurrent-upload queue-growth benchmark
- Experiment 003 multi-worker throughput and resource benchmark
- Experiment 004 three- and four-worker scaling comparison

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
