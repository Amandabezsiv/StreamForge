# Experiment 010: Database Loss During Lease Renewal

## Objective

Pause PostgreSQL while a worker is transcoding, prevent lease renewal for
longer than the lease duration, and verify that the stale owner cannot publish
or complete the job after database connectivity returns.

## Fault injection

- Lease: 6 seconds
- Renewal interval: 2 seconds
- PostgreSQL pause: 8 seconds
- Trigger: `TRANSCODING_STARTED`
- Recovery workers: 2

```bash
uv run python scripts/benchmark_database_loss_recovery.py
```

## Pass criteria

- Attempt 1 becomes `FAILED` with `WorkerLeaseExpired`.
- Attempt 2 completes and the video becomes `READY`.
- Exactly one output of each required type exists.
- No abandoned `.tmp` remains.
- History contains `JOB_ABANDONED`.

## Result

Recorded on 2026-08-26 local time (`2026-08-27T02:21:33Z`):

| Measurement | Result |
| --- | --- |
| Database outage | 8.158 seconds |
| Attempt 1 | `FAILED / WorkerLeaseExpired` |
| Attempt 2 | `COMPLETED` |
| Final video status | `READY` |
| Thumbnail outputs | 1 |
| Transcoded outputs | 1 |
| Remaining `.tmp` files | 0 |
| Outcome | **PASS** |

The renewal database call blocked while PostgreSQL was paused. When PostgreSQL
resumed, conditional ownership verification rejected the expired owner. The
stale worker could not publish its transcode or mark the job completed.

Recovery retained attempt 1 as failed, recorded `JOB_ABANDONED`, created attempt
2, reused the existing thumbnail output, and completed the 720p transcode.

## Conclusion

Temporary loss of the lease database behaves like loss of worker ownership.
This is deliberately conservative: a worker that cannot prove a current lease
must not publish or commit, even if its FFmpeg process completed successfully.
Availability is restored through a new attempt after PostgreSQL returns.

The test runner explicitly terminates the stale original worker after the
database resumes so it cannot also act as a recovery worker; this keeps the
experiment focused on one abandoned attempt and one recovery attempt.
