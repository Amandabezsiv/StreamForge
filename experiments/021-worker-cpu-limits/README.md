# Experiment 021: Worker CPU Limits

## Question

How does limiting each of four worker containers to one CPU affect sustainable
throughput, processing-time predictability, and pending-queue growth?

## Hypothesis

A one-CPU quota should prevent four automatic-thread FFmpeg processes from
consuming the whole host. Processing will be slower at low concurrency, but its
duration may vary less as load increases. The lower aggregate CPU allocation is
also expected to reduce the sustainable arrival rate.

## Controlled comparison

This experiment uses the Experiment 020 stack with one infrastructure change:

- four workers;
- **one CPU per worker container (four CPUs total)**;
- FFmpeg automatic thread allocation inside that container quota;
- the same `baseline-medium.mp4` fixture;
- an adaptively calibrated 2, 3, 4, and 5 arrivals/minute for 120 seconds each;
- the same host, API, PostgreSQL, local filesystem, LISTEN/NOTIFY, leases,
  Prometheus, and Grafana.

The quota covers the Python worker and all of its FFmpeg/ffprobe child
processes. Docker CPU percentages are therefore expected to approach 100% per
worker, or 400% across four workers, rather than the host's full logical-CPU
capacity.

## Metrics

- completed throughput and post-arrival drain time;
- processing-duration p50/p95;
- pending-queue growth slope and maximum depth;
- aggregate worker CPU mean/p95;
- failures, lease expirations, retries, and duplicate processing.

## Run

```bash
uv run python scripts/benchmark_sustained_arrival.py \
  --worker-cpu-limit 1 \
  --experiment-name 021-worker-cpu-limits \
  --output experiments/021-worker-cpu-limits/results.json
```

## Result

Recorded on 2026-08-31. All 28 measured videos completed, with no failures,
expired leases, retries, or duplicate processing.

An initial 12/minute calibration was stopped after it showed that a capped
worker needed roughly 80 seconds per video. That rate was already far above the
new capacity and would not locate its boundary. The measured sweep was therefore
centered on 2–5/minute and used a two-minute arrival window.

### Capacity and queue growth

| Arrival rate | Videos | Pending at end | Maximum pending | Queue slope | Drain tail | Classification |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2/min | 4 | 0 | 0 | 0.00/min | 25.75 s | Stable |
| 3/min | 6 | 0 | 0 | 0.00/min | 48.49 s | Stable |
| 4/min | 8 | 1 | 2 | 1.21/min | 90.14 s | Unstable |
| 5/min | 10 | 2 | 3 | 1.86/min | 99.71 s | Unstable |

The tested stability boundary is between 3 and 4 videos/minute. At 4/minute,
subtracting the 1.21 queued jobs/minute from arrivals estimates a service rate
of approximately 2.79 videos/minute. The equivalent estimate at 5/minute is
3.14/minute. Together, these measurements place saturated capacity around
3 videos/minute.

### Processing time and predictability

| Arrival rate | Queue wait p50 | Queue wait p95 | Processing mean | Processing p50 | Processing p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2/min | 0.010 s | 0.011 s | 56.07 s | 54.85 s | 60.59 s |
| 3/min | 0.009 s | 0.011 s | 63.60 s | 65.75 s | 69.70 s |
| 4/min | 0.011 s | 30.22 s | 78.73 s | 76.81 s | 90.20 s |
| 5/min | 25.04 s | 54.92 s | 73.11 s | 73.46 s | 82.64 s |

At stable load, the processing p95/p50 ratio was 1.10 at 2/minute and 1.06 at
3/minute. The quota therefore produced a relatively narrow duration range, but
around a much slower baseline. Once arrivals exceeded capacity, queue wait—not
pickup notification latency—became the rapidly growing tail.

### CPU utilization

Docker CPU is aggregated across all four workers. With one CPU per container,
the expected ceiling is approximately 400%.

| Arrival rate | Worker CPU mean | Worker CPU p95 | Worker CPU maximum | API CPU mean | PostgreSQL CPU mean |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2/min | 153.99% | 208.17% | 208.97% | 0.48% | 0.75% |
| 3/min | 223.26% | 399.19% | 409.56% | 0.54% | 0.90% |
| 4/min | 297.29% | 409.64% | 416.12% | 0.60% | 1.06% |
| 5/min | 332.45% | 412.32% | 421.77% | 0.64% | 1.22% |

Small excursions above 400% are Docker sampling noise. The p95 reaches the
quota ceiling from 3/minute onward, while the API and PostgreSQL remain near
1%. CPU allocated to media processing is still the limiting resource.

### Comparison with unlimited workers

| Result | Experiment 020: unlimited | Experiment 021: 1 CPU/worker |
| --- | ---: | ---: |
| Aggregate worker CPU available | Host, about 1200% | 400% |
| Conservative stable rate | 18/min | 3/min |
| Estimated saturated capacity | About 19/min | About 3/min |
| Stable-load processing p95 | 6.96 s at 18/min | 69.70 s at 3/min |

The CPU quota reduced estimated capacity by approximately 84%. That reduction
is much larger than the 67% reduction in aggregate CPU allowance. FFmpeg with
automatic thread allocation performs poorly when it creates a multithreaded
workload inside a one-CPU cgroup: thread coordination remains, but execution is
throttled to one core.

The limit improves resource isolation and makes host impact predictable, but it
does not improve absolute processing latency. It makes processing duration more
consistent only while arrivals remain below the much lower capacity boundary.

## Conclusion

One CPU per worker is too restrictive for the current automatic-thread FFmpeg
configuration. Four capped workers sustainably process roughly 3 medium videos
per minute, compared with roughly 19/minute when they may use the host. The
queue begins sustained growth at 4/minute.

A useful next experiment is to keep explicit container quotas but compare
`FFMPEG_THREADS=1` and `FFMPEG_THREADS=2`. Matching FFmpeg's internal parallelism
to its cgroup budget may reduce scheduling overhead and recover some capacity.

Raw measurements and one-second queue samples are stored in
[results.json](results.json).
