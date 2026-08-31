# Experiment 009: Worker Lease Recovery

## Objective

Verify that StreamForge detects a worker killed during processing, closes the
abandoned attempt, removes its temporary output, and completes the video through
a new processing attempt.

## Lease protocol

- A claim stores `claimed_by` and `lease_expires_at` in the atomic claim
  transaction.
- The owner renews the lease periodically from a separate database session.
- Every publication and status commit verifies ownership under a row lock.
- A worker scanning the queue locks expired jobs with `FOR UPDATE SKIP LOCKED`.
- Recovery marks the abandoned attempt `FAILED` with
  `WorkerLeaseExpired`, records `JOB_ABANDONED`, and creates attempt N+1 as
  `PENDING`.
- Recovery deletes same-directory `.*.tmp` artifacts.
- The retry reuses existing output rows, making thumbnail registration
  idempotent.

## Crash test

The test uses a short 6-second lease renewed every 2 seconds. It kills the only
worker with `SIGKILL` after `TRANSCODING_STARTED`, starts two recovery workers,
and waits for the same video to reach a terminal state.

```bash
uv run python scripts/benchmark_lease_recovery.py
```

## Pass criteria

- Attempt 1 becomes `FAILED` with `WorkerLeaseExpired`.
- Attempt 2 becomes `COMPLETED`.
- Video becomes `READY`.
- Exactly one thumbnail and one transcoded output are registered.
- No `.tmp` artifact remains.
- Processing history contains `JOB_ABANDONED`.

## Result

Recorded on 2026-08-26 local time (`2026-08-27T01:04:09Z`):

| Measurement | Result |
| --- | --- |
| Lease duration | 6 seconds |
| Renewal interval | 2 seconds |
| Recovery workers | 2 |
| Attempt 1 | `FAILED` — `WorkerLeaseExpired` |
| Attempt 2 | `COMPLETED` |
| Attempt 2 processing duration | 11.075 seconds |
| Final video status | `READY` |
| Registered thumbnail outputs | 1 |
| Registered transcoded outputs | 1 |
| Temporary files after recovery | 0 |
| Outcome | **PASS** |

### Timeline

```text
01:03:52.069  attempt 1 claimed
01:03:52.487  SIGKILL during transcoding
01:03:58.556  lease detected as expired; attempt 1 failed
01:03:58.576  attempt 2 claimed
01:04:09.657  attempt 2 completed; video ready
```

Attempt 2 processed for 11.075 seconds, longer than the 6-second lease. It
could complete only because its heartbeat renewed ownership during FFmpeg
processing.

### Persistent history

```text
Attempt 1: PROCESSING -> FAILED (WorkerLeaseExpired)
Attempt 2: PENDING -> PROCESSING -> COMPLETED
Video:     PROCESSING -> UPLOADED -> PROCESSING -> READY
```

The event stream contains `JOB_ABANDONED`, followed by the creation and normal
processing events for attempt 2. Recovery removed the abandoned 48-byte `.tmp`
file. The retry reused the existing thumbnail registration and produced one
valid `720p.mp4` output of 11,287,689 bytes.

## Conclusion

The lease protocol recovered a real hard worker failure without operator
intervention or duplicate output records. The failed attempt remains available
for processing history, while the new attempt has its own identity and attempt
number.

The next useful failure tests are loss of database connectivity during lease
renewal and a crash in the smaller window after atomic rename but before the
`VideoOutput` commit.
