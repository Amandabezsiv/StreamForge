# Experiment 007: Worker Failure During Processing

## Objective

Kill a worker while it is processing a video and inspect what remains in
PostgreSQL and the local filesystem.

## Failure point

The experiment uses one worker and the 60-second, 1920x1080 large fixture. It
waits until `TRANSCODING_STARTED` is visible in PostgreSQL, sends `SIGKILL` to
the worker container, stops it to prevent an automatic restart from changing
the evidence, and then captures database and filesystem state.

This point is useful because metadata extraction and thumbnail creation have
already committed, while transcoding has started but has not committed its
output registration.

## Hypothesis

- Video and job remain `PROCESSING`.
- Metadata remains stored on the video.
- The thumbnail file and database output remain stored.
- `TRANSCODING_STARTED` remains recorded.
- No `TRANSCODING_COMPLETED`, `JOB_COMPLETED`, or `JOB_FAILED` event exists.
- The 720p file may exist but be incomplete and unregistered.
- Restarted workers do not reclaim the job because they select only `PENDING`.

## Run

```bash
uv run python scripts/benchmark_worker_failure.py
```

The runner temporarily replaces the current workers with one worker, injects
the crash, records `results.json`, and restores four workers with three FFmpeg
threads each.

## Result

Recorded on 2026-08-25. The worker was killed with `SIGKILL` immediately after
the runner observed `TRANSCODING_STARTED`.

### PostgreSQL

| Item | Observed value |
| --- | --- |
| Video status | `PROCESSING` |
| Job status | `PROCESSING` |
| Job `finished_at` | `NULL` |
| Job error | none |
| Metadata | complete |
| Registered outputs | thumbnail only |

Persisted events, in order:

```text
JOB_CREATED
JOB_STARTED
METADATA_EXTRACTED
THUMBNAIL_CREATED
TRANSCODING_STARTED
```

There is no `TRANSCODING_COMPLETED`, `JOB_COMPLETED`, or `JOB_FAILED` event.
`SIGKILL` terminated Python before its exception handler could update the job.

### Filesystem

| File | Size | FFprobe validation | Registered in PostgreSQL |
| --- | ---: | --- | --- |
| `original.mp4` | 41,609,128 bytes | valid | represented by `Video.storage_key` |
| `thumbnail.jpg` | 73,325 bytes | valid | yes |
| `720p.mp4` | 0 bytes | invalid: `moov atom not found` | no |

The zero-byte `720p.mp4` is an orphaned partial artifact: it exists on disk but
is not a `VideoOutput`. This is possible because file creation and database
registration are not one atomic operation.

## Conclusion

The current atomic claim prevents duplicate acquisition, but it does not
provide crash recovery. After the claim transaction commits, a hard worker
failure leaves the job permanently in `PROCESSING`. Restarted workers ignore it
because acquisition selects only `PENDING` jobs.

The next reliability change should introduce a processing lease or heartbeat.
A recovery task can detect an expired lease, remove unregistered partial files,
and move the job back to `PENDING` or mark the attempt `FAILED`. Output files
should also be written to temporary names and atomically renamed only after
FFmpeg succeeds, preventing an incomplete file from appearing at the final
storage key.
