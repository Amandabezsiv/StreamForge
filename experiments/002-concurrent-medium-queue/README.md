# Experiment 002: Concurrent Medium Upload Queue Growth

## Context

Experiment 001 measured one video at a time. It did not show what happens to
queue wait when work arrives faster than a single worker can process it.

## Question

How does queue wait change when 10 medium videos are accepted concurrently and
processed by one worker?

## Hypothesis

All uploads should be accepted quickly, but the single worker must process jobs
sequentially. Queue wait should therefore grow with queue position by roughly
one medium-video processing duration per position.

## Configuration

```text
API instances:       1
PostgreSQL:          1
Workers:             1
Concurrent uploads: 10
Fixture:             baseline-medium.mp4
Duration:            30 seconds
Resolution:          1280x720
Actual size:         recorded by the runner
```

The runner uses a thread barrier so all clients begin their upload together.
The worker continues to use PostgreSQL polling and `FOR UPDATE SKIP LOCKED`.

## Run

```bash
docker compose up -d --build postgres api worker
uv run python scripts/benchmark_concurrent_queue.py --concurrency 10
```

The full result, including every video's queue position and timings, is written
to `results.json`.

## Result

Recorded on 2026-08-23:

```text
Uploads accepted:       10
Failed uploads/jobs:     0
All uploads accepted:    0.328 s
All videos ready:       38.307 s
Mean queue wait:        17.680 s
Median queue wait:      17.381 s
P95 queue wait:         34.174 s
Maximum queue wait:     34.174 s
```

### Queue growth

| Queue position | Queue wait | Processing | Transcoding | Total to ready |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1.822 s | 3.321 s | 3.088 s | 5.152 s |
| 2 | 5.159 s | 3.306 s | 3.066 s | 8.470 s |
| 3 | 8.478 s | 3.283 s | 3.054 s | 11.765 s |
| 4 | 11.773 s | 3.673 s | 3.435 s | 15.451 s |
| 5 | 15.456 s | 3.837 s | 3.501 s | 19.298 s |
| 6 | 19.305 s | 3.810 s | 3.580 s | 23.120 s |
| 7 | 23.127 s | 3.765 s | 3.506 s | 26.895 s |
| 8 | 26.900 s | 3.691 s | 3.460 s | 30.596 s |
| 9 | 30.603 s | 3.568 s | 3.326 s | 34.175 s |
| 10 | 34.174 s | 3.588 s | 3.286 s | 37.766 s |

### Summary statistics

| Metric | Mean | Median | Min | Max | P50 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Queue wait | 17.680 s | 17.381 s | 1.822 s | 34.174 s | 15.456 s | 34.174 s |
| Processing | 3.584 s | 3.631 s | 3.283 s | 3.837 s | 3.588 s | 3.837 s |
| Transcoding | 3.330 s | 3.381 s | 3.054 s | 3.580 s | 3.326 s | 3.580 s |
| Total to ready | 21.269 s | 21.209 s | 5.152 s | 37.766 s | 19.298 s | 37.766 s |

## Conclusion

The hypothesis was confirmed. FastAPI accepted the 10 uploads in less than one
second, but processing capacity remained limited to one video at a time. Each
queue position added approximately one processing duration to queue wait.

The tenth video waited 34.174 seconds before processing even though its own
processing took only 3.588 seconds. This demonstrates that API acceptance rate
and media-processing throughput are separate system capacities.

This result provides the first measured reason to test additional workers. A
following experiment can repeat the same workload with two workers and compare
queue-wait p50, p95, maximum, and total batch completion time.
