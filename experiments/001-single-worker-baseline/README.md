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

The size comparison uses three deterministic profiles:

| Profile               | Duration | Resolution | Target video bitrate |
| --------------------- | -------: | ---------: | -------------------: |
| `baseline-small.mp4`  |     10 s |    640x360 |               1 Mbps |
| `baseline-medium.mp4` |     30 s |   1280x720 |               3 Mbps |
| `baseline-large.mp4`  |     60 s |  1920x1080 |               6 Mbps |

Actual file sizes are recorded in the result because encoded size can vary
slightly with FFmpeg version and content complexity.

The fixture is generated under `storage/benchmark-fixtures/` and is not
committed to Git.

## Run

```bash
docker compose up -d --build postgres api worker
uv run python scripts/benchmark_e2e.py --regenerate-fixture
```

The command verifies the full upload-to-ready flow and writes the latest
measurement to `results.json`.

Run the three-size comparison:

```bash
uv run python scripts/benchmark_sizes.py --regenerate-fixtures
```

This writes `results-sizes.json`. The profiles run sequentially so they measure
media-size cost without concurrent jobs affecting queue time.

Run 20 repetitions for each size and calculate distribution statistics:

```bash
uv run python scripts/benchmark_repeated.py --runs 20
```

This writes `results-repeated-20.json` with every raw run plus mean, median,
minimum, maximum, p50, and nearest-rank p95 for queue wait, processing,
transcoding, and total time to ready. Runs remain sequential and use one worker.

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

| Measurement          |  Result |
| -------------------- | ------: |
| Upload duration      | 0.032 s |
| Queue wait time      | 1.208 s |
| Processing duration  | 1.015 s |
| Metadata duration    | 0.060 s |
| Thumbnail duration   | 0.139 s |
| Transcoding duration | 0.755 s |
| Total time to ready  | 2.257 s |
| Errors               |       0 |

The video reached `READY`, the job reached `COMPLETED`, all required metadata
was populated, and both required outputs were registered. These numbers are a
single-run functional baseline and do not yet describe system capacity.

## Size comparison result

Recorded on 2026-08-23 with one worker and sequential uploads:

| Profile | Actual size | Queue wait | Processing | Metadata | Thumbnail | Transcoding | Total to ready | Errors |
| ------- | ----------: | ---------: | ---------: | -------: | --------: | ----------: | -------------: | -----: |
| Small   |     1.34 MB |    1.291 s |    1.207 s |  0.068 s |   0.087 s |     1.037 s |        2.716 s |      0 |
| Medium  |    11.03 MB |    0.372 s |    3.266 s |  0.072 s |   0.175 s |     2.999 s |        3.648 s |      0 |
| Large   |    41.61 MB |    1.174 s |    6.893 s |  0.062 s |   0.254 s |     6.482 s |        8.075 s |      0 |

Every video reached `READY`, every job reached `COMPLETED`, all required
metadata was extracted, and thumbnail and transcoded outputs were registered.

The first observation is that ffprobe metadata time stayed nearly constant for
these fixtures, while transcoding time increased with duration and resolution
and represented most of the processing duration. Queue wait varies with the
worker's polling position and should be examined over repeated runs before
drawing a performance conclusion.

## Repeated result: 20 runs per size

Recorded on 2026-08-23 using one worker and 60 sequential end-to-end runs.
There were no processing errors. All values below are seconds. P95 uses the
nearest-rank method; with 20 observations it is the 19th ordered value.

### Small

| Metric         |  Mean | Median |   Min |   Max |   P50 |   P95 |
| -------------- | ----: | -----: | ----: | ----: | ----: | ----: |
| Queue wait     | 1.798 |  1.795 | 1.263 | 1.978 | 1.793 | 1.975 |
| Processing     | 1.225 |  1.219 | 1.173 | 1.305 | 1.219 | 1.305 |
| Transcoding    | 1.043 |  1.037 | 1.002 | 1.101 | 1.036 | 1.090 |
| Total to ready | 3.038 |  3.021 | 2.575 | 3.266 | 3.021 | 3.178 |

### Medium

| Metric         |  Mean | Median |   Min |   Max |   P50 |   P95 |
| -------------- | ----: | -----: | ----: | ----: | ----: | ----: |
| Queue wait     | 1.819 |  1.810 | 1.697 | 1.960 | 1.809 | 1.954 |
| Processing     | 3.157 |  3.150 | 3.104 | 3.236 | 3.144 | 3.211 |
| Transcoding    | 2.908 |  2.901 | 2.876 | 3.001 | 2.901 | 2.953 |
| Total to ready | 5.018 |  4.994 | 4.847 | 5.443 | 4.979 | 5.167 |

### Large

| Metric         |  Mean | Median |   Min |    Max |   P50 |    P95 |
| -------------- | ----: | -----: | ----: | -----: | ----: | -----: |
| Queue wait     | 1.751 |  1.743 | 1.652 |  1.900 | 1.742 |  1.899 |
| Processing     | 8.070 |  8.280 | 7.511 |  8.813 | 8.280 |  8.613 |
| Transcoding    | 7.715 |  7.909 | 7.175 |  8.456 | 7.900 |  8.269 |
| Total to ready | 9.841 |  9.997 | 9.223 | 10.553 | 9.964 | 10.485 |

### Interpretation

- Queue wait remains close to the worker's two-second polling interval. This is
  polling latency, not media-processing cost.
- Mean processing time grows from 1.225 seconds for small videos to 8.070
  seconds for large videos.
- Transcoding consumes approximately 85% to 96% of processing time and is the
  dominant stage in this workload.
- The small and medium processing distributions are narrow. Large processing
  has a wider range of 7.511 to 8.813 seconds, suggesting greater sensitivity
  to CPU scheduling or thermal/resource conditions.
- These sequential results describe service time for one worker. They do not
  yet measure throughput or queue growth under concurrent uploads.

All raw observations and full-precision statistics are retained in
`results-repeated-20.json`.

### Conclusion

The single-worker baseline showed stable processing latency across repeated runs. PostgreSQL polling introduced approximately 1.8 seconds of fixed job acquisition latency across all fixture sizes. As workload size increased, transcoding became the dominant processing cost, accounting for approximately 85% of processing time for the small fixture, 92% for medium, and 96% for large. This suggests that job acquisition latency primarily affects small workloads, while media transcoding becomes the dominant scalability concern for larger workloads.
