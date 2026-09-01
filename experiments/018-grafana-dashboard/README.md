# Experiment 018: Grafana Dashboard Provisioning

## Objective

Provide a reproducible dashboard for the Prometheus metrics introduced in
Experiment 017 and verify that it is automatically available after
`docker compose up`.

## Deployment

- Grafana 13.2.0
- Provisioned Prometheus datasource with UID `prometheus`
- Provisioned dashboard with UID `streamforge-overview`
- Anonymous Viewer access for local development
- Persistent Grafana data volume

## Panels

| Panel | PromQL purpose |
| --- | --- |
| Jobs pending | Current PostgreSQL-backed pending gauge |
| Jobs processing | Current PostgreSQL-backed processing gauge |
| Jobs completed rate | Sum of per-worker completion rates |
| Job failure rate | Sum of per-worker failure rates |
| Pickup latency p95 | Quantile over globally aggregated histogram buckets |
| Processing duration p95 | Quantile over globally aggregated histogram buckets |
| Lease expirations | Increase across workers over the dashboard range |
| Retries | Increase across workers over the dashboard range |
| Videos processed per minute | Five-minute completion rate multiplied by 60 |

## Validation

Grafana reported:

- database status `ok`;
- version `13.2.0`;
- Prometheus datasource health `OK`;
- dashboard UID `streamforge-overview`;
- exactly nine provisioned panels.

Open the dashboard at:

<http://localhost:3000/d/streamforge-overview/streamforge-overview>

## Conclusion

The dashboard and datasource are infrastructure as code. A new environment can
reproduce the same observability view without manual Grafana configuration.
Worker metrics are aggregated in PromQL, so scaling the worker service does not
require dashboard edits.

Anonymous access is appropriate only for this local engineering laboratory. It
must be disabled before Grafana is exposed on a shared or public network.
