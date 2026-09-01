# Experiment 017: Prometheus Observability Adoption

## Objective

Validate continuous collection of StreamForge queue, worker outcome, latency,
lease, and retry metrics through Prometheus.

## Deployment

- Prometheus 3.14.0
- One FastAPI scrape target
- Four worker scrape targets discovered through Docker DNS
- Five-second scrape and DNS refresh intervals

## Validation

Prometheus reported every configured target as healthy:

| Job | Healthy targets |
| --- | ---: |
| `streamforge-api` | 1 |
| `streamforge-worker` | 4 |

Every requested series appeared in the query API:

| Metric | Series |
| --- | ---: |
| `streamforge_jobs_pending` | 1 |
| `streamforge_jobs_processing` | 1 |
| `streamforge_jobs_completed_total` | 4 |
| `streamforge_jobs_failed_total` | 4 |
| `streamforge_job_pickup_duration_seconds_count` | 4 |
| `streamforge_job_processing_duration_seconds_count` | 4 |
| `streamforge_worker_lease_expired_total` | 4 |
| `streamforge_job_retries_total` | 4 |

A fresh small-video upload was then processed to validate non-zero worker
observations:

| Measurement | Value |
| --- | ---: |
| Queue wait | 0.024561 s |
| Processing duration | 1.745583 s |
| `sum(streamforge_jobs_completed_total)` | 1 |
| `sum(streamforge_job_pickup_duration_seconds_count)` | 1 |
| `sum(streamforge_job_processing_duration_seconds_count)` | 1 |

The existing development database exposed nine legacy `PROCESSING` rows with
no `claimed_by` or `lease_expires_at`. This is pre-existing experiment data, not
a Prometheus error. The database-backed gauge correctly made the inconsistent
durable state visible and gives a concrete subject for a future data-integrity
repair experiment.

## Conclusion

The complete collection path works:

```text
API database gauges ───────────────┐
                                   ├─> Prometheus ─> PromQL
worker counters and histograms ────┘
```

Docker DNS discovery found all four scaled workers, and a real completed job
changed the expected counter and histogram series. Lease and retry counters are
present at zero and will increase only when their corresponding recovery paths
execute.

Metric ownership, reset behavior, aggregation queries, alternatives, and
security consequences are documented in
[`ADR-002`](../../docs/adr/ADR-002-prometheus-observability.md).
