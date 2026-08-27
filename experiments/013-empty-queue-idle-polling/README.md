# Experiment 013: Empty-Queue Idle Polling

## Objective

Measure how much PostgreSQL and worker overhead is generated when workers poll
an empty queue.

## Matrix

Worker scaling at the default interval:

- 0 workers at 2 seconds (database baseline)
- 1 worker at 2 seconds
- 4 workers at 2 seconds
- 8 workers at 2 seconds

Polling interval scaling with 4 workers:

- 0.1, 0.5, 1, 2, and 5 seconds

Polling interval scaling with 8 workers:

- 0.1, 0.5, 1, and 2 seconds

Each configuration is measured for 30 seconds after a 2-second warm-up.

## Metrics

- Empty polls and polls per second
- PostgreSQL commits, rollbacks, and total transactions per second
- Mean server-side execution latency of the real queue polling queries
- PostgreSQL CPU and memory
- Combined worker CPU and memory
- Open database connections before and after measurement

An empty worker cycle performs an expired-lease recovery transaction and a
pending-job query that rolls back when no job is found. Connection checkout and
session handling also contribute transactions. The experiment therefore records
PostgreSQL counters instead of estimating the cost only from application calls.

## Run

```bash
uv run python scripts/benchmark_idle_polling.py
```

## Result

Recorded on 2026-08-27. No jobs were pending and no StreamForge application
rows were inserted, updated, or deleted during any measured window.

### Worker scaling at the default interval

| Workers | Poll interval | Empty polls/s | DB transactions/s | PostgreSQL CPU mean | Worker CPU mean | DB connections |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 2 s | 0.000 | 0.433 | 0.62% | 0.00% | 1 |
| 1 | 2 s | 0.533 | 2.367 | 0.47% | 0.01% | 2 |
| 4 | 2 s | 2.133 | 8.500 | 0.46% | 0.01% | 5 |
| 8 | 2 s | 4.267 | 16.833 | 0.70% | 0.53% | 9 |

At the normal two-second interval, idle overhead grows approximately linearly
with worker count. Four idle workers added about 8.07 transactions per second
over the database baseline, while PostgreSQL mean CPU remained close to the
baseline noise in this 30-second sample.

### Poll-interval scaling with four workers

| Poll interval | Expected polls/s | Observed polls/s | DB transactions/s | PostgreSQL CPU mean | Worker CPU mean |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.1 s | 40.0 | 38.667 | 154.966 | 3.79% | 7.62% |
| 0.5 s | 8.0 | 8.000 | 32.300 | 1.16% | 1.46% |
| 1 s | 4.0 | 4.000 | 16.367 | 0.98% | 0.98% |
| 2 s | 2.0 | 2.133 | 8.500 | 0.46% | 0.01% |
| 5 s | 0.8 | 0.933 | 3.700 | 0.57% | 0.17% |

### Poll-interval scaling with eight workers

| Poll interval | PostgreSQL CPU mean | DB transactions/s | Query latency mean | Worker CPU mean | DB connections | Empty polls/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 s | 0.70% | 16.833 | 0.030 ms | 0.53% | 9 | 4.267 |
| 1 s | 1.21% | 32.300 | 0.034 ms | 1.61% | 9 | 8.000 |
| 500 ms | 2.14% | 64.600 | 0.035 ms | 3.46% | 9 | 16.067 |
| 100 ms | 7.27% | 312.366 | 0.030 ms | 16.46% | 9 | 78.000 |

`pg_stat_statements` measured server-side execution time for the actual
`processing_jobs` `SELECT` statements. This excludes network and client-side
SQLAlchemy time. Mean query latency remained essentially flat, so PostgreSQL
was not saturated by this matrix. The cost appeared as linearly increasing
transaction volume and CPU instead of slower queries or more connections.

The observed rate is close to `workers / poll interval`; boundary effects are
more visible when only a few polls fit into the 30-second window. PostgreSQL
recorded approximately four transactions per empty poll: about three commits
and one rollback. The rollback is the unsuccessful pending-job acquisition;
the commits include recovery and connection/session transaction handling.

### Conclusion

The current default of two seconds is inexpensive at four workers on this
machine: approximately 2.13 empty polls and 8.50 database transactions per
second, with no measurable row writes. Reducing the interval to 100 ms makes
job pickup more responsive but creates about 155 transactions per second and
raises combined idle worker CPU to 7.62%. With eight workers, the same interval
creates about 312 transactions per second and uses 7.27% PostgreSQL CPU plus
16.46% worker CPU, although query latency remains stable. Keep the two-second
default for the current deployment. If worker count grows substantially or
sub-second pickup latency becomes necessary, replace independent polling with
notification or backoff rather than continuously shortening the interval.

Raw measurements are stored in [results.json](results.json).
