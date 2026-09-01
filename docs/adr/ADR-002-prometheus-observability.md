# ADR-002: Prometheus Metrics for Processing Observability

Status: Accepted

Date: 2026-08-31

## Context

Experiments 001–016 measured queue behavior with one-off benchmark scripts and
Docker resource samples. Those measurements explain controlled experiments but
do not continuously expose queue depth, worker outcomes, processing latency,
lease recovery, or retry behavior.

The API and workers run as separate processes, and workers may be horizontally
scaled. A metric design must preserve that process boundary without making
PostgreSQL or local files a shared Prometheus counter store.

## Decision

Adopt the Prometheus text exposition format through `prometheus-client` and run
a pinned Prometheus server in Docker Compose. Provision a pinned Grafana server,
Prometheus datasource, and version-controlled StreamForge dashboard through
Docker Compose mounts.

The API exposes `/metrics` on port 8000. Its queue gauges query PostgreSQL when
scraped, making the durable job table the source of truth:

- `streamforge_jobs_pending`
- `streamforge_jobs_processing`

Each worker exposes an internal HTTP metrics server on port 9000:

- `streamforge_jobs_completed_total`
- `streamforge_jobs_failed_total`
- `streamforge_job_pickup_duration_seconds`
- `streamforge_job_processing_duration_seconds`
- `streamforge_worker_lease_expired_total`
- `streamforge_job_retries_total`

Prometheus uses DNS service discovery for `worker`, allowing every scaled
container to be scraped. Worker counters and histogram buckets are aggregated
across instances with PromQL. The API gauges must not be summed with worker
copies because they are exposed only by the API target.

The Grafana dashboard is a presentation layer over Prometheus and does not query
application databases directly. Local development enables anonymous Viewer
access; external environments must add authentication and network controls.

## Metric Semantics

Worker counters are process-lifetime counters. They reset when a worker
container restarts. Prometheus retains previous samples, and queries should use
`rate()` or `increase()` when measuring activity over time.

Counters are incremented only after the corresponding database transaction
commits. Processing histograms observe completed or registered failed attempts.
Pickup latency is observed after a successful atomic claim. An expired lease
increments the failed, lease-expired, and retry counters because one abandoned
attempt fails and one new attempt is created.

Example multi-worker queries:

```promql
sum(rate(streamforge_jobs_completed_total[5m]))

sum(increase(streamforge_worker_lease_expired_total[1h]))

histogram_quantile(
  0.95,
  sum by (le) (rate(streamforge_job_processing_duration_seconds_bucket[5m]))
)
```

## Alternatives Considered

### Derive every metric from PostgreSQL

This provides durable totals but adds queries and cannot represent fine-grained
histogram distributions without storing every observation.

### Pushgateway

Workers are long-running scrapeable services, so push-based metrics add state
and lifecycle complexity without solving a current problem.

### Shared multiprocess metric files

Containers do not share a process lifecycle or reliable cleanup semantics.
Scraping each worker directly is simpler and preserves instance labels.

## Consequences

- Queue state and processing behavior are continuously queryable.
- Every worker consumes one internal HTTP port and one Prometheus scrape target.
- Worker restarts reset local counters, so raw totals must not be interpreted as
  durable business totals.
- Prometheus DNS discovery may briefly retain terminated worker targets until
  its refresh interval expires.
- Provisioned dashboards are reviewed as code and restored from the repository
  on container recreation.
- Metrics currently have no authentication because they are intended for the
  local development network. External deployments must restrict Prometheus and
  Grafana access.

## Revisit When

- metrics need durable business-level totals independent of Prometheus;
- cardinality requirements introduce labels such as codec or resolution;
- Prometheus availability or retention becomes operationally important;
- the platform moves to Kubernetes service discovery;
- alerting and dashboards require Alertmanager or Grafana.
