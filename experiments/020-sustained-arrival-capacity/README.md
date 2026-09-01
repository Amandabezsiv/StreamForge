# Experiment 020: Sustained Arrival Rate vs Processing Capacity

## Question

What video arrival rate can StreamForge sustain without the pending queue
growing indefinitely?

## Method

Submit the same medium fixture at fixed rates while four workers process jobs:

- 12 videos/minute
- 16 videos/minute
- 18 videos/minute
- 20 videos/minute
- 22 videos/minute

Each rate is injected for 60 seconds. After arrivals stop, the experiment waits
for the scoped queue to drain before starting the next rate. Queue depth is
sampled every second.

A rate is provisionally classified as stable when:

- the pending-depth slope over the final 80% of the arrival window is at most
  0.5 jobs/minute; and
- no more than four jobs remain pending when arrivals stop.

This one-minute sweep establishes an exploratory bound. A longer soak test is
required before treating the result as a production capacity guarantee.

## Controlled configuration

- Four workers with FFmpeg automatic threads
- `baseline-medium.mp4`: 30 seconds, 1280×720, approximately 11.03 MB
- Same host, PostgreSQL, API, and local filesystem
- `LISTEN/NOTIFY` enabled with 30-second polling fallback
- Lease recovery enabled: 30-second lease, 10-second renewal
- Prometheus and Grafana enabled

## Metrics

- Pending queue depth and growth slope
- Pending depth when arrivals stop and maximum depth
- Time required to drain after arrivals stop
- Queue-wait and processing-duration p50/p95
- Worker/API/PostgreSQL/Prometheus/Grafana CPU and memory
- Upload latency, failures, leases, retries, and duplicates
- Disk space consumed by each rate

## Run

```bash
uv run python scripts/benchmark_sustained_arrival.py
```

## Result

Recorded on 2026-08-31. All 88 submitted videos completed successfully, with
no processing failures, expired leases, retries, or duplicate processing.

### Capacity sweep

| Arrival rate | Videos | Pending at end | Maximum pending | Queue slope | Drain after arrivals | Classification |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 12/min | 12 | 0 | 0 | 0.00/min | 0.00 s | Stable |
| 16/min | 16 | 0 | 0 | 0.00/min | 0.51 s | Stable |
| 18/min | 18 | 0 | 0 | 0.00/min | 2.53 s | Stable |
| 20/min | 20 | 0 | 1 | 0.38/min | 7.09 s | Marginal/stable by rule |
| 22/min | 22 | 2 | 2 | 3.13/min | 13.65 s | Unstable |

The configured classifier identifies 20 videos/minute as the highest stable
tested rate and 22/minute as the lowest unstable rate. However, 20/minute had a
small positive slope and increasing drain tail, so 18/minute is the highest
rate with no measured pending growth in this one-minute experiment.

### Latency and processing contention

| Arrival rate | Queue wait p50 | Queue wait p95 | Processing p50 | Processing p95 |
| ---: | ---: | ---: | ---: | ---: |
| 12/min | 0.009 s | 0.058 s | 3.370 s | 3.585 s |
| 16/min | 0.011 s | 0.101 s | 3.765 s | 3.862 s |
| 18/min | 0.017 s | 0.038 s | 5.961 s | 6.961 s |
| 20/min | 0.036 s | 0.786 s | 9.166 s | 12.781 s |
| 22/min | 0.292 s | 6.172 s | 12.163 s | 13.747 s |

Pickup remains fast because `LISTEN/NOTIFY` wakes workers immediately. The
rapid increase in processing duration is caused by overlapping multithreaded
FFmpeg processes competing for the same CPU. This creates a nonlinear capacity
curve: raising the arrival rate increases concurrency, and increased
concurrency makes each job slower.

### Component utilization

| Arrival rate | Worker CPU mean | Worker CPU p95 | API CPU mean | PostgreSQL CPU mean |
| ---: | ---: | ---: | ---: | ---: |
| 12/min | 545.29% | 956.30% | 0.72% | 0.92% |
| 16/min | 797.83% | 987.44% | 1.21% | 1.13% |
| 18/min | 937.81% | 1107.12% | 1.67% | 1.53% |
| 20/min | 1010.57% | 1122.73% | 1.59% | 1.56% |
| 22/min | 1032.24% | 1124.01% | 1.29% | 1.52% |

Worker CPU approaches the host's approximately 1200% logical capacity at and
above 18 videos/minute. API and PostgreSQL CPU remain close to 1–2%, confirming
that the sustainable-rate boundary is set by FFmpeg compute rather than job
creation, notification, queue locking, or database throughput.

### Estimated capacity

At 22 arrivals/minute, pending depth grew at 3.13 jobs/minute. Subtracting queue
growth from arrival rate gives an observed service rate of approximately:

```text
22.00 arrivals/minute - 3.13 queued/minute = 18.87 completed/minute
```

This agrees with Experiment 019's saturated throughput of 17.82 videos/minute.
The practical sustainable capacity on this host is therefore approximately
19 videos/minute, with the following operational interpretation:

- **18/minute:** conservative tested rate with no observed pending growth;
- **20/minute:** marginal and requires a longer soak before being considered
  safe;
- **22/minute:** demonstrably above capacity; queue growth is unbounded while
  that arrival rate continues.

### Observability audit

The final database total and active-worker Prometheus counter both reported 88
completed jobs. During the 20/minute phase, subtraction of two raw Prometheus
instant values reported only eight new completions because stale series from
workers replaced at experiment startup aged out between snapshots. No worker
restarted during the sweep. The benchmark now filters counter snapshots through
the currently healthy `up{job="streamforge-worker"}` targets to prevent stale
container identities from affecting future deltas.

### Storage

The five rates consumed approximately 1.90 GB of additional filesystem space.
About 8.4 GB remained after the experiment. Storage did not fail, but future
large benchmark campaigns should introduce an explicit artifact-retention or
cleanup policy.

## Conclusion

StreamForge can conservatively sustain 18 medium videos per minute with four
automatic-thread FFmpeg workers on this machine. The transition occurs between
20 and 22 videos/minute, and the measured saturated service capacity is roughly
19 videos/minute.

The next experiment should hold arrival rate near 20/minute for 10–15 minutes.
That soak test will determine whether the small 20/minute slope is measurement
noise or genuine long-term backlog growth. A second useful comparison is fixed
FFmpeg thread allocation, because automatic multithreading makes processing
duration deteriorate sharply as concurrent jobs increase.

Raw measurements and one-second queue samples are stored in
[results.json](results.json).
