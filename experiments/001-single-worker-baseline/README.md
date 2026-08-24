# Experiment 001: Single Worker Baseline

## Question

How long does one uploaded video spend waiting and processing in the initial
FastAPI, PostgreSQL, local-storage, and single-worker architecture?

## Architecture

```text
FastAPI -> PostgreSQL + local storage -> one Python worker -> FFmpeg/ffprobe
```

## Fixed fixture

- Generated with FFmpeg `testsrc` and a sine-wave audio source
- Duration: 10 seconds
- Resolution: 1280x720
- Frame rate: 30 FPS
- Video codec: H.264
- Audio codec: AAC

The fixture is generated under `storage/benchmark-fixtures/` and is not
committed to Git.

## Run

```bash
docker compose up -d --build postgres api worker
uv run python scripts/benchmark_e2e.py --regenerate-fixture
```

The command verifies the full upload-to-ready flow and writes the latest
measurement to `results.json`.

## Metrics

- `queue_wait_time`: job creation until worker acquisition
- `processing_duration`: worker processing start until completion
- `metadata_duration`: ffprobe execution
- `thumbnail_duration`: thumbnail FFmpeg execution
- `transcoding_duration`: 720p FFmpeg execution
- `total_time_to_ready`: video registration until `READY`
- `errors`: persisted processing error code and message, if any

This is a functional baseline with one video, not a concurrency-capacity test.
Later runs should introduce fixed concurrency levels and report percentiles.

## First result

Recorded on 2026-08-23 using the local Docker environment:

| Measurement | Result |
| --- | ---: |
| Upload duration | 0.032 s |
| Queue wait time | 1.208 s |
| Processing duration | 1.015 s |
| Metadata duration | 0.060 s |
| Thumbnail duration | 0.139 s |
| Transcoding duration | 0.755 s |
| Total time to ready | 2.257 s |
| Errors | 0 |

The video reached `READY`, the job reached `COMPLETED`, all required metadata
was populated, and both required outputs were registered. These numbers are a
single-run functional baseline and do not yet describe system capacity.
