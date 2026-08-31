# Experiment 016: LISTEN/NOTIFY Failure Recovery

## Objective

Verify that a job is not stranded when PostgreSQL sends its notification while
the worker's dedicated listener connection is unavailable.

## Failure sequence

```text
worker LISTEN connection is active
             ↓
terminate only the listener backend
             ↓
verify that no job listener is connected
             ↓
create and commit a PENDING job
             ↓
NOTIFY is emitted with no receiver
             ↓
fallback timeout expires
             ↓
worker queries PostgreSQL and atomically claims the job
             ↓
job completes and listener reconnects
```

The worker process and its normal SQLAlchemy connection are not terminated.
Only the dedicated connection identified by application name
`streamforge-worker-listener` is disconnected with `pg_terminate_backend()`.

## Checks

- No listener exists when the job is created
- Queue wait is bounded by the configured fallback interval
- Exactly one processing attempt completes
- `JOB_STARTED` and `JOB_COMPLETED` each occur once
- Required outputs are registered
- The listener reconnects with a new PostgreSQL backend PID

The experiment uses a five-second fallback to keep the test short. Production
uses 30 seconds, so the same recovery mechanism has a longer worst-case delay.

## Run

```bash
uv run python scripts/benchmark_listen_notify_failure.py
```

## Result

Recorded on 2026-08-30.

| Measurement | Result |
| --- | ---: |
| Terminated listener PID | 3151 |
| Listeners present when job was created | 0 |
| Job queue wait | 4.991 s |
| Configured fallback | 5.000 s |
| Time from upload to READY | 7.385 s |
| Replacement listener PID | 3167 |
| Processing attempts | 1 |
| `JOB_STARTED` events | 1 |
| `JOB_COMPLETED` events | 1 |
| Duplicate processing | No |

The completed job registered both required outputs:

- `THUMBNAIL`
- `TRANSCODED_VIDEO`

All experiment checks passed:

- the notification was emitted while no listener existed;
- fallback polling claimed the job within the configured bound;
- the original processing attempt completed exactly once;
- the listener reconnected using a new backend connection.

### Conclusion

PostgreSQL notifications are transient and are not retained for disconnected
listeners. The missed notification did not lose the job because the durable
`PENDING` row remained the source of truth. Once the fallback timeout expired,
the worker queried the queue and claimed that row through the normal locking
transaction.

The tradeoff is recovery latency: a missed notification may wait for nearly the
entire fallback interval. This experiment used five seconds and observed 4.991
seconds. With the production 30-second fallback, worst-case recovery is close
to 30 seconds. The interval can be selected according to the acceptable missed-
notification delay and idle polling cost measured by Experiments 013–015.

Raw measurements are stored in [results.json](results.json).
