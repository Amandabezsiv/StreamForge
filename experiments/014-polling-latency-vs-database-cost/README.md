# Experiment 014: Polling Latency vs Database Cost

## Objective

Measure how reducing the worker polling interval improves real job pickup
latency and how much additional idle load that creates on PostgreSQL.

## Method

One worker is tested at polling intervals of 2 seconds, 1 second, 500 ms, and
100 ms. Each configuration has two separate phases:

1. Observe an empty queue for 30 seconds and measure PostgreSQL transactions,
   CPU, query execution latency, connections, worker CPU, and empty polls.
2. Submit 20 small videos serially, with a deterministic random delay before
   each submission, and measure `ProcessingJob.queue_wait_seconds`.

Waiting for each video to finish before the next submission keeps the queue
empty and isolates pickup latency from processing backlog. Separating the idle
cost window prevents FFmpeg CPU and job-state writes from being counted as
polling overhead.

Query latency is the server-side mean reported by `pg_stat_statements` for the
real `processing_jobs` polling `SELECT` statements. It does not include network
or SQLAlchemy client time.

## Run

```bash
uv run python scripts/benchmark_polling_latency_cost.py
```

## Result

Recorded on 2026-08-27 with one worker and 20 completed video jobs per polling
interval. No jobs failed.

### Pickup latency and database cost

| Poll interval | Pickup mean | Pickup p50 | Pickup p95 | Pickup max | DB transactions/s | PostgreSQL CPU mean |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 s | 1.060 s | 1.079 s | 1.773 s | 1.902 s | 2.433 | 0.54% |
| 1 s | 0.639 s | 0.609 s | 0.902 s | 1.082 s | 4.500 | 0.77% |
| 500 ms | 0.307 s | 0.335 s | 0.500 s | 0.503 s | 8.367 | 0.80% |
| 100 ms | 0.081 s | 0.075 s | 0.173 s | 0.197 s | 38.967 | 1.97% |

### Idle-polling details

| Poll interval | Empty polls/s | Query latency mean | Worker CPU mean | DB connections |
| ---: | ---: | ---: | ---: | ---: |
| 2 s | 0.533 | 0.044 ms | 0.03% | 3 |
| 1 s | 1.000 | 0.060 ms | 0.25% | 3 |
| 500 ms | 2.000 | 0.030 ms | 0.66% | 3 |
| 100 ms | 9.667 | 0.033 ms | 3.25% | 3 |

The occasional pickup measurement above the configured interval includes
worker scheduling and the claim transaction around the sleep boundary. This is
real enqueue-to-claim time, not just time spent sleeping.

### Tradeoff relative to the two-second default

| Poll interval | P50 pickup reduction | P95 pickup reduction | DB transaction multiplier |
| ---: | ---: | ---: | ---: |
| 1 s | 43.6% | 49.1% | 1.85x |
| 500 ms | 69.0% | 71.8% | 3.44x |
| 100 ms | 93.1% | 90.3% | 16.01x |

### Conclusion

Shorter polling intervals reduce pickup latency approximately in proportion to
the interval, but database traffic rises inversely with it. The 500 ms setting
is the best compromise measured here: p95 pickup fell from 1.773 seconds to
0.500 seconds while PostgreSQL handled only 8.37 transactions per second and
used 0.80% mean CPU.

The 100 ms setting provides the lowest latency, but the improvement from 500 ms
to 100 ms costs another 4.66 times as many database transactions. PostgreSQL
query execution latency stayed below 0.060 ms and connections stayed fixed, so
the database was not saturated in this single-worker experiment. For the
current project, use 500 ms only when sub-second job start is required;
otherwise retain the less expensive two-second default.

Raw measurements and all 80 pickup samples are stored in
[results.json](results.json).
