# Experiment 019: Observed End-to-End High Load

## Question

When the workload increases substantially, which component becomes the first
measurable system constraint?

## Controlled configuration

- Four workers
- 50 videos submitted concurrently
- The same `baseline-medium.mp4` fixture for every request
- Same host and local filesystem
- Same PostgreSQL database
- PostgreSQL `LISTEN/NOTIFY` enabled
- 30-second job leases with 10-second renewal
- Prometheus and Grafana enabled
- FFmpeg automatic thread allocation

The fixture is 30 seconds, 1280×720, and approximately 11.03 MB. The batch
therefore uploads approximately 552 MB of original video data before generated
artifacts are included.

## Metrics

- Upload acceptance duration and latency distribution
- Batch duration and videos per minute
- Queue-wait, processing, transcoding, and total-ready p50/p95/max
- Completed and failed jobs
- Duplicate jobs, starts, and outputs
- CPU, memory, and block I/O by service
- PostgreSQL transactions, connections, and processing-job query latency
- Prometheus completed, failed, pickup, processing, lease, and retry deltas

## Run

```bash
uv run python scripts/benchmark_observed_high_load.py
```

## Result

Recorded on 2026-08-31.

### Batch result

| Metric | Result |
| --- | ---: |
| Videos submitted | 50 |
| Completed | 50 |
| Failed | 0 |
| All uploads accepted | 1.647 s |
| Batch duration | 168.386 s |
| Throughput | 17.816 videos/minute |
| Lease expirations | 0 |
| Retries | 0 |
| Duplicate processing | None |

### Latency

| Metric | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Upload | 0.797 s | 0.797 s | 0.861 s | 0.882 s |
| Queue wait | 75.033 s | 75.530 s | 148.055 s | 160.522 s |
| Processing | 13.024 s | 13.444 s | 14.665 s | 15.015 s |
| Transcoding | 12.282 s | 12.631 s | 13.839 s | 14.218 s |
| Total time to ready | 88.072 s | 89.050 s | 161.444 s | 166.304 s |

The first four jobs started almost immediately. Later jobs waited in waves of
four, so queue wait dominated end-to-end latency as the batch exceeded worker
capacity.

### Resource utilization by component

| Component | CPU mean | CPU p95 | CPU maximum | Memory p95 |
| --- | ---: | ---: | ---: | ---: |
| Four workers | 1013.14% | 1095.61% | 1116.74% | 2066.0 MiB |
| API | 23.20% | 33.90% | 179.80% | 219.8 MiB |
| PostgreSQL | 5.21% | 9.42% | 29.15% | 58.4 MiB |
| Prometheus | 1.43% | 5.96% | 6.63% | 45.6 MiB |
| Grafana | 2.79% | 14.13% | 18.89% | 743.7 MiB |

The host exposes 12 logical CPUs, represented as approximately 1200% maximum
Docker CPU. Worker CPU reached 91.3% of that capacity at p95 and 93.1% at the
observed maximum. The monitoring stack consumed little CPU relative to FFmpeg.

### Storage and database

Container block-I/O deltas during the measured window:

| Component | Read | Write |
| --- | ---: | ---: |
| Workers | 0 MB reported | 476.0 MB |
| API | 0 MB reported | 529.9 MB |
| PostgreSQL | 6.46 MB | 3.19 MB |
| Prometheus | 0 MB reported | 0.11 MB |
| Grafana | 0 MB reported | 0.10 MB |

Zero reported worker/API block reads indicate host page-cache service or Docker
accounting behavior, not an absence of file reads. The system wrote about 1.01
GB through the API and workers without a visible throughput stall.

PostgreSQL observations:

| Metric | Result |
| --- | ---: |
| Connections before | 10 |
| Connections after | 14 |
| Transactions during batch | 19,382 |
| Mean `processing_jobs` SELECT execution | 0.114 ms |
| PostgreSQL CPU mean | 5.21% |

The large transaction count is primarily caused by the benchmark repeatedly
requesting video and job status for up to 50 videos. Despite that intentionally
chatty observation pattern, PostgreSQL query execution remained far below one
millisecond and CPU remained low.

### Prometheus consistency

| Counter delta | Result |
| --- | ---: |
| Completed jobs | 50 |
| Failed jobs | 0 |
| Pickup histogram observations | 50 |
| Processing histogram observations | 50 |
| Lease expirations | 0 |
| Retries | 0 |

Prometheus matched the 50 database-scoped experiment jobs exactly.

## Bottleneck analysis

### First limiting component: worker CPU / FFmpeg

The four FFmpeg workers were the first constrained component:

- p95 worker CPU was 1095.61% on a host with 1200% logical capacity;
- median processing duration rose to 13.444 seconds;
- throughput was bounded at 17.816 videos/minute;
- excess work accumulated in PostgreSQL, producing 148.055 seconds queue-wait
  p95 and 160.522 seconds maximum queue wait.

Queue growth is the symptom; insufficient media-processing capacity relative
to arrival rate is the cause. `LISTEN/NOTIFY` removed idle pickup delay but
cannot make CPU-bound transcoding faster.

### Components not yet limiting

- **API:** accepted approximately 552 MB across all 50 requests in 1.647
  seconds; upload p95 was 0.861 seconds.
- **PostgreSQL:** mean CPU was 5.21%, p95 CPU was 9.42%, and measured query
  execution averaged 0.114 ms despite frequent status polling.
- **Memory:** workers used about 2.07 GiB at p95 on a host exposing about 31 GiB
  to Docker.
- **Prometheus and Grafana:** their combined mean CPU was about 4.2%, so
  observability did not materially reduce processing capacity.
- **Leases and locking:** no lease expired, no retry was created, and no
  duplicate processing was detected.

### Comparison with the earlier 10-video four-worker run

| Metric | 10 videos | 50 videos | Change |
| --- | ---: | ---: | ---: |
| Throughput | 19.976/min | 17.816/min | -10.8% |
| Processing p50 | 10.949 s | 13.444 s | +22.8% |
| Queue-wait p95 | 23.031 s | 148.055 s | +542.8% |

The larger batch did not reveal a PostgreSQL or observability ceiling. It kept
all four FFmpeg processes busy for long enough to expose sustained CPU
saturation and a throughput decline.

## Conclusion

On this machine, the next optimization should target media-processing CPU, not
PostgreSQL. The most useful follow-up experiment is to repeat the same 50-video
batch with bounded FFmpeg thread allocation and two, three, and four workers.
That will test whether reducing thread contention improves sustained throughput
and queue tail latency. Storage throughput should be measured with host-level
disk telemetry in a later experiment because Docker block-I/O counters alone
cannot distinguish page-cache effects from physical-device saturation.

Raw measurements and all 50 per-video results are stored in
[results.json](results.json).
