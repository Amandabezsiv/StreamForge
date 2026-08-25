# Experiment 004: Worker Scaling on a 6-Core Host

## Context

Experiment 003 showed that moving from one to two workers increased throughput
by 22.5%, but median processing time increased by 62% because both FFmpeg
processes competed for CPU resources.

The host has 6 physical cores and 12 hardware threads. FFmpeg is itself
multithreaded, so adding worker processes may eventually reduce or reverse the
throughput gain.

## Question

Does throughput continue increasing with three and four workers, or does CPU
contention dominate on this host?

## Hypothesis

Three and four workers may reduce queue wait because more jobs begin earlier,
but each video should take longer to process. Throughput gains should diminish
and may plateau because unrestricted FFmpeg processes already use many cores.

## Controlled variables

- 10 concurrent medium videos per run
- `baseline-medium.mp4`, 30 seconds, 1280x720, approximately 11.03 MB
- Same API, PostgreSQL, local storage, worker code, and FFmpeg settings
- Same host described in Experiment 003
- Only worker count changes: three workers, then four workers

## Metrics

- Batch duration and videos per minute
- Queue-wait p50, p95, and maximum
- Processing-duration p50 and p95
- Total-time-to-ready p50 and p95
- Combined worker CPU, memory, and block I/O
- Errors and duplicate-processing audit

## Run

```bash
uv run python scripts/benchmark_multi_worker.py \
  --workers 3 \
  --concurrency 10 \
  --output experiments/004-worker-scaling/results-3-workers.json

uv run python scripts/benchmark_multi_worker.py \
  --workers 4 \
  --concurrency 10 \
  --output experiments/004-worker-scaling/results-4-workers.json
```

## Three-worker result

Recorded on 2026-08-25:

```text
Workers:                    3
Concurrent medium videos:  10
Completed:                  10
Failed:                      0
All uploads accepted:       0.303 s
Batch duration:             33.734 s
Throughput:                 17.786 videos/minute
Duplicate processing:       none detected
```

### Latency

| Metric | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Queue wait | 12.298 s | 10.253 s | 28.907 s | 28.907 s |
| Processing duration | 8.908 s | 9.242 s | 9.986 s | 9.986 s |
| Total time to ready | 21.286 s | 20.163 s | 33.340 s | 33.340 s |

### Resources

Docker statistics combine the three worker containers:

| Resource | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| CPU | 914.91% | 1071.71% | 1087.55% | 1087.55% |
| Memory | 1235.50 MiB | 1527.30 MiB | 1535.50 MiB | 1535.50 MiB |

Container block-I/O deltas during the measured window:

```text
Read:   0 MB reported
Write: 65.1 MB
```

The zero block-read delta means these reads were served from the host page
cache or were not attributed as container block I/O. It does not mean FFmpeg
read no input data. Docker produced 18 samples at an observed mean interval of
approximately 2.018 seconds.

### Duplicate-processing audit

```text
Jobs audited:                         10
Jobs with multiple JOB_STARTED events: 0
Duplicate output types for a video:     0
Videos with multiple jobs:              0
```

No duplicate processing was detected.

## Four-worker result

Recorded on 2026-08-25:

```text
Workers:                    4
Concurrent medium videos:  10
Completed:                  10
Failed:                      0
All uploads accepted:       0.431 s
Batch duration:             30.036 s
Throughput:                 19.976 videos/minute
Duplicate processing:       none detected
```

### Four-worker latency

| Metric | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Queue wait | 10.059 s | 12.247 s | 23.031 s | 23.031 s |
| Processing duration | 10.151 s | 10.949 s | 11.745 s | 11.745 s |
| Total time to ready | 20.227 s | 22.637 s | 29.422 s | 29.422 s |

### Four-worker resources

| Resource | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| CPU | 896.45% | 1065.73% | 1109.22% | 1109.22% |
| Memory | 1575.45 MiB | 2107.50 MiB | 2122.40 MiB | 2122.40 MiB |

Container block-I/O deltas during the measured window:

```text
Read:  17.5 MB
Write: 86.8 MB
```

Docker produced 16 samples at an observed mean interval of approximately 2.030
seconds. The lower mean CPU and memory values include startup and idle-tail
samples; p50 and maximum better represent the period when all four FFmpeg
processes were active.

### Four-worker duplicate-processing audit

```text
Jobs audited:                         10
Jobs with multiple JOB_STARTED events: 0
Duplicate output types for a video:     0
Videos with multiple jobs:              0
```

No duplicate processing was detected.

## Complete comparison

| Metric | 1 worker | 2 workers | 3 workers | 4 workers |
| --- | ---: | ---: | ---: | ---: |
| Batch duration | 38.307 s | 31.275 s | 33.734 s | 30.036 s |
| Videos per minute | 15.663 | 19.185 | 17.786 | 19.976 |
| Queue-wait p50 | 15.456 s | 13.283 s | 10.253 s | 12.247 s |
| Queue-wait p95 | 34.174 s | 24.904 s | 28.907 s | 23.031 s |
| Processing p50 | 3.588 s | 5.813 s | 9.242 s | 10.949 s |
| Processing p95 | 3.837 s | 6.174 s | 9.986 s | 11.745 s |
| Total-ready p50 | 19.298 s | 19.105 s | 20.163 s | 22.637 s |
| Total-ready p95 | 37.766 s | 30.806 s | 33.340 s | 29.422 s |
| Errors | 0 | 0 | 0 | 0 |
| Duplicate processing | 0 | 0 | 0 | 0 |

## Three-worker conclusion

Throughput peaked at two workers for the configurations tested so far. Adding
a third worker reduced throughput from 19.185 to 17.786 videos per minute and
increased batch duration from 31.275 to 33.734 seconds.

Queue-wait p50 improved because three jobs could start together, but queue-wait
p95 became worse than with two workers. Median processing duration increased
from 5.813 seconds with two workers to 9.242 seconds with three workers. This is
strong evidence that unrestricted multithreaded FFmpeg processes are competing
for the host's 6 physical cores and 12 hardware threads.

## Final conclusion

Four workers produced the shortest batch duration and highest throughput in
this single run, but the gain over two workers was small:

```text
Two workers:  19.185 videos/minute
Four workers: 19.976 videos/minute
Improvement:   4.1%
```

That 4.1% throughput gain came with major per-video costs. Compared with two
workers, four workers increased processing p50 by approximately 88.4% and
total-time-to-ready p50 by approximately 18.5%. Peak combined worker memory
increased from about 1.10 GiB to 2.12 GiB.

Four workers improved queue-wait p95 and total-to-ready p95 because four jobs
could start per wave. However, each active FFmpeg process ran much more slowly
while competing for the same 6 physical cores. The host CPU reached roughly
1066% at p50, close to saturating its 12 hardware threads.

For this workload, two workers are the better balance between throughput,
median latency, and resource consumption. Four workers are only preferable if
batch completion and tail latency matter more than individual processing time
and memory efficiency. Three workers performed worse than both two and four in
this single sample, so repeated runs would be required before treating that
non-monotonic result as stable.
