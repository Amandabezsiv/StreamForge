# Experiment 011: Crash After Publication, Before Database Commit

## Objective

Kill a worker after `720p.mp4` has been atomically renamed into place but before
the corresponding `VideoOutput` and completion state are committed.

## Fault injection

The worker uses an experiment-only 30-second delay immediately after atomic
publication. Its ownership row lock remains held during this delay. The runner
watches for the final file and sends `SIGKILL`, forcing PostgreSQL to roll back
the open ownership transaction.

```bash
uv run python scripts/benchmark_post_publish_crash.py
```

## Pass criteria

- The crash snapshot contains a valid final `720p.mp4`.
- The crash snapshot has no transcoded `VideoOutput` row.
- The abandoned attempt becomes `FAILED / WorkerLeaseExpired`.
- The new attempt completes idempotently and the video becomes `READY`.
- Exactly one thumbnail and one transcoded output are registered.
- No temporary artifact remains.

## Result

Recorded on 2026-08-26 local time (`2026-08-27T02:22:44Z`):

### Crash snapshot

| Observation | Result |
| --- | --- |
| Final `720p.mp4` | present |
| Final file size | 11,287,689 bytes |
| FFprobe validation | valid |
| Transcoded `VideoOutput` | absent |
| Job status | `PROCESSING` |
| Completion events | absent |

The atomic rename succeeded, but `SIGKILL` rolled back the still-open database
transaction. This produced the expected valid orphan final file: storage was
ahead of PostgreSQL.

### Recovery

| Observation | Result |
| --- | --- |
| Attempt 1 | `FAILED / WorkerLeaseExpired` |
| Attempt 2 | `COMPLETED` |
| Final video status | `READY` |
| Thumbnail outputs | 1 |
| Transcoded outputs | 1 |
| Remaining `.tmp` files | 0 |
| Outcome | **PASS** |

Attempt 2 safely replaced the complete orphan through another atomic rename and
registered it. Idempotent output registration reused the existing thumbnail
row, so no duplicate storage keys or output types were created.

## Conclusion

The filesystem and PostgreSQL cannot share one transaction, so atomic rename
does not eliminate the publication-to-registration consistency window. The
lease retry path makes that window recoverable: a complete orphan final file is
safe to replace, and registration happens only under current ownership.

A future optimization could FFprobe the orphan final file and register it
without retranscoding. The current approach favors simple deterministic
reprocessing over that optimization.
