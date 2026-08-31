# Experiment 015: LISTEN/NOTIFY vs Polling

## Objective

Verify that PostgreSQL `LISTEN/NOTIFY` reduces job pickup latency and idle
database traffic while preserving polling as a fallback.

## Compared modes

- Polling only every 2 seconds
- Polling only every 500 ms
- Polling only every 100 ms
- `LISTEN/NOTIFY` with a 30-second polling fallback

All modes use one worker and the same atomic
`SELECT FOR UPDATE SKIP LOCKED` claim transaction. Notifications only wake the
worker; they do not carry or assign ownership of a job.

Each mode has a separate 30-second empty-queue cost window followed by 20 real
small-video uploads. A deterministic random delay within a common two-second
window prevents submissions from being synchronized to the polling cycle.

## Metrics

- Job pickup latency mean, p50, p95, and maximum
- Empty polls and PostgreSQL transactions per second
- PostgreSQL and worker CPU
- Polling-query server latency
- Database connections
- Processing failures

## Run

```bash
uv run python scripts/benchmark_listen_notify.py
```

## Result

Recorded on 2026-08-30 with 20 successfully completed video jobs per mode. No
processing or notification-listener errors occurred.

### Pickup latency

| Mode | Mean | P50 | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Polling 2 s | 1.058 s | 1.122 s | 1.957 s | 2.008 s |
| Polling 500 ms | 0.243 s | 0.171 s | 0.536 s | 0.631 s |
| Polling 100 ms | 0.056 s | 0.042 s | 0.107 s | 0.109 s |
| LISTEN/NOTIFY, 30 s fallback | 0.039 s | 0.012 s | 0.093 s | 0.336 s |

The LISTEN/NOTIFY maximum coincided with a 343 ms upload/API request outlier.
Worker logs contained no listener failure or reconnect error. P50 and p95 are
more representative of notification delivery in this run.

### Empty-queue database cost

| Mode | Empty polls/s | DB transactions/s | PostgreSQL CPU mean | Worker CPU mean | Query latency mean | DB connections |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Polling 2 s | 0.533 | 2.433 | 0.72% | 0.00% | 0.041 ms | 3 |
| Polling 500 ms | 2.000 | 8.500 | 1.13% | 1.01% | 0.046 ms | 4 |
| Polling 100 ms | 9.333 | 37.800 | 2.59% | 4.58% | 0.045 ms | 4 |
| LISTEN/NOTIFY, 30 s fallback | 0.067 | 0.533 | 0.46% | 0.11% | 0.028 ms | 5 |

LISTEN requires one dedicated PostgreSQL connection per worker. The connection
count therefore increases, but that idle connection replaces repeated recovery
and acquisition transactions.

### Comparison

Against the original two-second polling mode, LISTEN/NOTIFY:

- reduced p50 pickup latency by 98.9%;
- reduced p95 pickup latency by 95.3%;
- reduced idle database transactions by 78.1%.

Against 100 ms polling, LISTEN/NOTIFY:

- reduced p50 pickup latency by 71.0%;
- reduced p95 pickup latency by 13.2%;
- reduced idle database transactions by 98.6%, or approximately 71 times.

### Conclusion

LISTEN/NOTIFY provides lower pickup latency than aggressive polling while also
reducing idle PostgreSQL work. It should be the normal worker wake-up path. The
30-second polling timeout remains necessary for missed notifications,
connection failures, worker startup races, and lease-recovery checks.

Notifications do not replace the queue or transfer ownership. Every awakened
worker still competes through the existing atomic
`SELECT FOR UPDATE SKIP LOCKED` transaction, so duplicate-processing protection
is unchanged. With many workers, one notification may wake several listeners;
that thundering-herd behavior should be measured in a later multi-worker
experiment.

Raw measurements and all 80 pickup samples are stored in
[results.json](results.json).
