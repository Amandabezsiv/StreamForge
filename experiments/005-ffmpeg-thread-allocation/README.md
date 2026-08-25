# Experiment 005: FFmpeg Thread Allocation

## Context

Experiment 004 showed that increasing unrestricted workers raises per-video
processing time because every FFmpeg process automatically uses many CPU
threads. The test host has 6 physical cores and 12 hardware threads.

## Question

Can explicit FFmpeg thread allocation improve throughput or latency by reducing
competition between worker processes?

## Hypothesis

Limiting FFmpeg threads should reduce per-process CPU competition. More workers
may then process concurrently without the severe service-time increase observed
with automatic FFmpeg threading.

## Configurations

| Workers | FFmpeg threads per process |
| ---: | ---: |
| 2 | auto |
| 2 | 4 |
| 3 | 4 |
| 4 | 3 |

FFmpeg `-threads 0` represents auto mode. A positive `FFMPEG_THREADS` value is
passed to both thumbnail generation and transcoding.

## Controlled workload

- 10 concurrent medium videos per configuration
- 30 seconds, 1280x720, approximately 11.03 MB each
- Same API, PostgreSQL, local storage, FFmpeg encoding settings, and host
- Worker count and FFmpeg threads are the only changed variables

## Metrics

- Batch duration and videos per minute
- Queue-wait p50 and p95
- Processing-duration p50 and p95
- Total-time-to-ready p50 and p95
- Combined worker CPU, memory, and block I/O
- Errors and duplicate processing

## Run

```bash
docker compose build worker
uv run python scripts/benchmark_ffmpeg_threads.py
```

The matrix runner writes one full result per configuration and a concise
`results-summary.json` comparison.

## Results

Run recorded on 2026-08-25. Each configuration was measured once, so small
differences should be treated as indicative rather than conclusive.

### Throughput and latency

| Workers | FFmpeg threads | Batch duration | Videos/min | Queue p50 | Queue p95 | Processing p50 | Processing p95 | Ready p50 | Ready p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | auto | 33.496 s | 17.912 | 13.128 s | 26.328 s | 6.515 s | 6.618 s | 19.695 s | 32.922 s |
| 2 | 4 | 36.049 s | 16.644 | 14.301 s | 28.525 s | 7.004 s | 7.124 s | 21.391 s | 35.548 s |
| 3 | 4 | 37.056 s | 16.192 | 10.323 s | 30.911 s | 10.379 s | 10.483 s | 20.738 s | 36.574 s |
| 4 | 3 | 35.119 s | 17.085 | 12.997 s | 26.391 s | 12.212 s | 13.311 s | 26.283 s | 34.533 s |

### Resources and correctness

CPU and memory values combine all worker containers. Block I/O is the change
in Docker's counters during the measured batch.

| Workers | FFmpeg threads | CPU p50 | CPU p95 | Memory p50 | Memory p95 | Read | Write | Errors | Duplicates |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | auto | 1075.55% | 1096.23% | 1021.30 MiB | 1027.00 MiB | 0 B | 65.2 MB | 0 | 0 |
| 2 | 4 | 929.69% | 951.50% | 724.60 MiB | 728.90 MiB | 0 B | 41.4 MB | 0 | 0 |
| 3 | 4 | 945.51% | 1070.16% | 1086.20 MiB | 1090.80 MiB | 0 B | 62.1 MB | 0 | 0 |
| 4 | 3 | 1031.92% | 1085.49% | 1402.10 MiB | 1410.40 MiB | 0 B | 72.5 MB | 0 | 0 |

## Interpretation

For this workload, **2 workers with automatic FFmpeg threading** produced the
best batch time and throughput in this experiment. Changing the same two
workers to four threads reduced median combined CPU by about 13.6% and median
memory by about 29.1%, but throughput also fell by about 7.1%.

Adding workers did not recover that loss. With 3 or 4 workers, CPU competition
increased per-video processing time: processing p50 rose from 7.004 seconds for
2 workers/4 threads to 10.379 seconds for 3 workers/4 threads and 12.212 seconds
for 4 workers/3 threads. Four workers shortened some queue waits but had the
worst median time to ready.

The result does not prove that auto mode is always optimal. It shows that on
this 6-core/12-thread host, for ten copies of this 30-second 720p fixture, the
tested limits traded performance for lower resource use. Repeating each case
multiple times and testing longer or higher-resolution inputs would provide a
more reliable production tuning decision.

## Artifacts

- `results-2-workers-auto.json`
- `results-2-workers-4-threads.json`
- `results-3-workers-4-threads.json`
- `results-4-workers-3-threads.json`
- `results-summary.json`
