# Experiment 006: Concurrent Job Acquisition

## Objective

Deliberately make many contenders compete for the same small set of pending
jobs and verify that no job can be claimed twice.

## Concurrency mechanism

The worker claims a job inside one PostgreSQL transaction:

```sql
SELECT ...
FROM processing_jobs
WHERE status = 'PENDING'
ORDER BY created_at
LIMIT 1
FOR UPDATE SKIP LOCKED;
```

It then changes the job to `PROCESSING`, changes its video to `PROCESSING`,
records one `JOB_STARTED` event, and commits. `FOR UPDATE` gives the selected
row an exclusive lock. `SKIP LOCKED` makes competing workers immediately look
for a different unlocked job instead of waiting for the first worker.

## Test design

- Create 2 pending jobs directly in PostgreSQL.
- Release 16 contenders simultaneously with a thread barrier.
- Every contender uses its own SQLAlchemy session and database connection.
- Hold each successful row lock for 1 second before commit. This diagnostic
  delay widens the race window and is disabled in normal workers.
- Record every return value and independently audit job status and
  `JOB_STARTED` event counts in PostgreSQL.

The normal Docker workers must be stopped during the experiment so they cannot
claim the two diagnostic jobs.

## Pass criteria

- Exactly 2 successful claims and 14 empty claims.
- The 2 claimed job IDs are different.
- Every created job ends in `PROCESSING`.
- Every created job has exactly one `JOB_STARTED` event.
- No duplicate claim or duplicate start event is detected.

## Run

```bash
docker compose stop worker
uv run python scripts/benchmark_job_acquisition.py
docker compose up -d --scale worker=4 worker
```

The full result is written to `results.json`. The experiment creates diagnostic
database rows with zero-byte storage references; it does not invoke FFprobe or
FFmpeg.

## Result

Recorded on 2026-08-25:

| Measurement | Result |
| --- | ---: |
| Pending jobs | 2 |
| Simultaneous contenders | 16 |
| Diagnostic lock hold | 1.000 s |
| Successful claims | 2 |
| Empty claims | 14 |
| Unique claimed job IDs | 2 |
| Duplicate claims | 0 |
| Duplicate `JOB_STARTED` events | 0 |
| Batch duration | 1.056 s |
| Outcome | **PASS** |

Both successful contenders took approximately 1.050 seconds because they held
their row locks for the configured diagnostic delay. The 14 unsuccessful
contenders returned in approximately 0.032–0.037 seconds. They did not wait for
the winning transactions to commit, demonstrating the non-blocking behavior of
`SKIP LOCKED`.

The database audit found both jobs in `PROCESSING` and exactly one
`JOB_STARTED` event for each job. No two contenders returned the same job ID.

## Conclusion

The acquisition transaction is atomic under the tested contention. `FOR
UPDATE` prevents two database sessions from owning the same job row, while
`SKIP LOCKED` allows losing workers to continue without waiting. The status
change and `JOB_STARTED` event are committed in that same claim transaction,
before media processing starts.

The contenders run in one Python process but use separate SQLAlchemy sessions
and PostgreSQL connections. Row locking is enforced by PostgreSQL, so this
exercises the same database boundary used by separate worker containers. A
future fault-injection experiment should test worker termination immediately
before and after the claim commit, because atomic claiming does not by itself
recover jobs abandoned in `PROCESSING`.
