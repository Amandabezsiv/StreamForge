# Experiment 003: Two Workers

## Context

Experiment 002 submitted 10 medium videos concurrently to one worker. All
uploads were accepted in 0.328 seconds, but queue-wait p95 reached 34.174
seconds and the batch took 38.307 seconds to become ready.

## Question

How do throughput, queue wait, resource usage, and duplicate-processing safety
change when the same workload is processed by two workers?

## Hypothesis

Two workers should reduce batch duration and queue wait because two jobs can be
processed concurrently. CPU, memory, and I/O pressure should increase. The
PostgreSQL `FOR UPDATE SKIP LOCKED` acquisition should prevent duplicate job
processing.

## Controlled variables

- The application and worker code are unchanged.
- The same 10 medium videos are uploaded concurrently.
- PostgreSQL, local storage, API instance, and host remain unchanged.
- Only the worker count changes from one to two.

## Host environment

Recorded on 2026-08-25:

```text
CPU:                 Intel Core i5-11400F @ 2.60 GHz
Physical cores:      6
Hardware threads:   12
Maximum turbo:       4.4 GHz
L3 cache:           12 MiB
Host memory:        31 GiB
Host swap:           8 GiB
Docker-visible CPUs: 12
Docker-visible RAM:  31.2 GiB
Kernel:              Linux 6.14.0-33-generic x86_64
Project filesystem:  ext4 on /dev/sda3
Primary disk:        Kingston A400 120 GB SATA SSD
```

Docker data, PostgreSQL data, and StreamForge local media storage are all on
the same filesystem. CPU and I/O results are specific to this machine and
should not be compared directly with results from different hardware.

## Metrics

- Batch duration
- Videos per minute
- Queue-wait p50, p95, and maximum
- Processing-duration p50 and p95
- Total-time-to-ready p50 and p95
- Combined worker CPU usage
- Combined worker memory usage
- Combined worker block I/O
- Errors
- Duplicate `JOB_STARTED` events, outputs, or jobs

## Run

```bash
uv run python scripts/benchmark_multi_worker.py --workers 2 --concurrency 10
```

The runner recreates and scales only the worker service, samples Docker
resources during the batch, audits PostgreSQL for duplicate processing, and
writes the full result to `results.json`.

## Result

Recorded on 2026-08-25:

```text
Workers:                    2
Concurrent medium videos:  10
Completed:                  10
Failed:                      0
Batch duration:             31.275 s
Throughput:                 19.185 videos/minute
Duplicate processing:       none detected
```

### Latency

| Metric | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: |
| Queue wait | 13.283 s | 24.904 s | 24.904 s |
| Processing duration | 5.813 s | 6.174 s | 6.174 s |
| Total time to ready | 19.105 s | 30.806 s | 30.806 s |

### Resources

Docker resource statistics combine both worker containers:

| Resource | Mean | P95 | Maximum |
| --- | ---: | ---: | ---: |
| CPU | 848.44% | 1141.19% | 1141.19% |
| Memory | 789.89 MiB | 1099.70 MiB | 1099.70 MiB |

Container block-I/O deltas during the measured window:

```text
Read:  63.8 MB
Write: 65.2 MB
```

CPU percentages can exceed 100% because FFmpeg uses multiple CPU cores. Docker
provided 17 samples at an observed mean interval of approximately 2.013
seconds. Block I/O represents Docker container counters and may not include
reads served from the host page cache.

### Duplicate-processing audit

The audit examined all 10 jobs after the batch:

```text
Jobs with multiple JOB_STARTED events: 0
Duplicate output types for a video:     0
Videos with multiple jobs:              0
```

`FOR UPDATE SKIP LOCKED` successfully prevented both workers from acquiring the
same pending job in this run.

## Comparison with Experiment 002

| Metric | 1 worker | 2 workers | Change |
| --- | ---: | ---: | ---: |
| Batch duration | 38.307 s | 31.275 s | -18.4% |
| Videos per minute | 15.663 | 19.185 | +22.5% |
| Queue-wait p50 | 15.456 s | 13.283 s | -14.1% |
| Queue-wait p95 | 34.174 s | 24.904 s | -27.1% |
| Processing p50 | 3.588 s | 5.813 s | +62.0% |
| Total-to-ready p50 | 19.298 s | 19.105 s | -1.0% |
| Total-to-ready p95 | 37.766 s | 30.806 s | -18.4% |
| Errors | 0 | 0 | unchanged |
| Duplicate processing | 0 | 0 | unchanged |

## Conclusion

Adding a second worker improved batch throughput and reduced worst-case queue
wait, but it did not double capacity. Median processing duration increased by
approximately 62%, showing CPU contention between the two multithreaded FFmpeg
processes. The extra worker trades higher CPU and memory pressure for a 22.5%
throughput improvement.

This is a measurable diminishing return. Before adding more workers, the next
experiment should record host CPU topology and test FFmpeg thread limits; a
third unrestricted worker may add contention faster than throughput.
