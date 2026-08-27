# Experiment 012: PostgreSQL Queue Under Concurrency

## Objective

Determine how atomic queue acquisition scales as more workers compete for
pending jobs, and identify where PostgreSQL claim throughput stops improving
while acquisition latency rises.

## Isolation

This experiment does not run FFprobe, FFmpeg, or filesystem writes. Every run
preloads 1,000 diagnostic `Video` and `ProcessingJob` rows, measures only the
production `acquire_pending_job` transaction, audits the result, and deletes the
diagnostic rows.

## Matrix

- Claimant concurrency: 1, 2, 4, 8, 16, 32, 64
- Runs per level: 3
- Pending jobs per run: 1,000
- Claim strategy: `SELECT FOR UPDATE SKIP LOCKED`

## Metrics

- Claims per second
- Successful claim latency: mean, median, p50, p95, p99, maximum
- Empty-queue claim latency
- Peak benchmark PostgreSQL connections
- Transaction and tuple counter deltas
- Errors, duplicate claims, and duplicate `JOB_STARTED` events

The saturation knee is the first level where throughput improves by less than
10% while p95 claim latency grows by more than 25% relative to the previous
level.

## Run

```bash
uv run python scripts/benchmark_postgres_queue_concurrency.py
```

Normal Docker workers are stopped during measurement and restored afterward.

## Result

Recorded on 2026-08-26 local time (`2026-08-27T02:46:35Z`). All 21 runs passed.

| Claimers | Claims/s | P95 claim | P99 claim | Peak connections | Errors | Duplicates |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 179.76 | 6.56 ms | 7.10 ms | 2 | 0 | 0 |
| 2 | 319.24 | 7.47 ms | 8.19 ms | 3 | 0 | 0 |
| 4 | **390.52** | 13.36 ms | 15.23 ms | 5 | 0 | 0 |
| 8 | 376.80 | 28.05 ms | 35.11 ms | 9 | 0 | 0 |
| 16 | 374.48 | 59.09 ms | 74.66 ms | 17 | 0 | 0 |
| 32 | 369.07 | 119.26 ms | 140.68 ms | 33 | 0 | 0 |
| 64 | 353.21 | 246.68 ms | 294.39 ms | 65 | 0 | 0 |

Values are means across three runs except peak connections. PostgreSQL allowed
100 connections in this environment.

## Saturation analysis

The measured saturation knee is **8 concurrent claimers**:

```text
4 claimers: 390.52 claims/s, p95 13.36 ms
8 claimers: 376.80 claims/s, p95 28.05 ms
```

Moving from 4 to 8 claimers reduced throughput by 3.5% while increasing p95
latency by 2.10×. This satisfies the experiment's saturation definition.

Concurrency above 8 added no queue capacity. Throughput stayed near 369–374
claims/s at 16 and 32, then fell to 353 claims/s at 64. At 64 claimers, p95 was
18.5× the 4-claimer p95 while throughput was 9.6% lower.

## Correctness

Across 21,000 total claims:

```text
Acquisition errors:             0
Duplicate job claims:           0
Duplicate JOB_STARTED events:   0
Jobs left outside PROCESSING:   0
```

`FOR UPDATE SKIP LOCKED` remained correct under every tested concurrency level.

## Conclusion

PostgreSQL was not overwhelmed in the sense of errors or connection exhaustion:
64 claimers used 65 benchmark connections against `max_connections = 100`.
However, the queue was already latency-saturated at 8 claimers, and 4 claimers
provided the highest throughput for this transaction and dataset.

For the current design, adding more than 4 simultaneous queue claimers wastes
connections and increases claim latency without increasing capacity. Before
testing beyond 64, the next optimization should examine the pending-job query
plan and a partial/composite queue index, then repeat this exact matrix. A
larger queue depth should also be tested because ordering and filtering costs
may change with substantially more than 1,000 pending rows.
