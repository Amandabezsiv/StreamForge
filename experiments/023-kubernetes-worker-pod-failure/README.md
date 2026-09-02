# Experiment 023: Kubernetes Worker Pod Failure and Recovery

## Question

When the worker Pod running FFmpeg disappears, how quickly does Kubernetes
replace the Pod, and how quickly does StreamForge recover the abandoned leased
job without duplicate outputs or temporary artifacts?

## Failure sequence

```text
four Ready worker Pods
        ↓
one Pod claims an uploaded job
        ↓
TRANSCODING_STARTED is committed
        ↓
force-delete the owning Pod
        ↓
Kubernetes creates and readies a replacement
        ↓
the abandoned job lease expires
        ↓
an idle worker detects it through fallback polling
        ↓
attempt 1 FAILED + attempt 2 PENDING/PROCESSING
        ↓
video finishes READY
```

## Configuration

- Local single-node Kind cluster from Experiment 022
- Four worker replicas
- 30-second ownership lease
- 10-second lease renewal
- 30-second fallback poll
- PostgreSQL `LISTEN/NOTIFY` enabled
- Shared media PVC
- `baseline-medium.mp4`
- Forced Pod deletion with zero grace period during FFmpeg transcoding

The normal lease and polling settings are intentionally preserved. Kubernetes
Pod replacement and application-level job recovery are separate mechanisms:
creating a replacement Pod does not transfer ownership of the old job.

## Metrics

- time from deletion request until replacement Pod creation;
- time from deletion request until replacement Pod Ready;
- time from deletion until `JOB_ABANDONED` lease-expiry registration;
- time from lease expiry until the retry attempt starts;
- time from deletion until the video becomes `READY`;
- processing-attempt count and worker retry counter;
- duplicate registered outputs;
- temporary files immediately after the crash and after recovery;
- final video state and worker replica health.

## Run

```bash
uv run python scripts/benchmark_kubernetes_worker_failure.py
```

## Result

Recorded on 2026-09-02. The owning worker Pod was force-deleted immediately
after the `TRANSCODING_STARTED` event. The experiment passed every recovery and
consistency assertion.

### Recovery timeline

| Event after Pod deletion | Time |
| --- | ---: |
| Replacement Pod observed | 0.151 s |
| Replacement Pod Ready observed | 3.784 s |
| Expired lease registered | 31.611 s |
| Recovery attempt started | 31.631 s |
| Delay from lease expiry to retry start | 0.019 s |
| Video returned to `READY` | 45.526 s |
| Total upload-to-ready time | 45.916 s |

Kubernetes API creation and Ready timestamps were both rounded to the same
whole second as the deletion request. The table therefore uses the benchmark's
monotonic client observations for the subsecond creation and readiness timing.

### State and consistency

| Check | Result |
| --- | --- |
| Worker replicas after deletion | 4/4 Ready |
| Attempt 1 | `FAILED`, `WorkerLeaseExpired` |
| Attempt 2 | `COMPLETED` |
| Retry counter delta | `+1` |
| Lease-expiration counter delta | `+1` |
| Final video state | `READY` |
| Registered outputs | 1 thumbnail, 1 transcoded video |
| Duplicate storage keys | None |
| Temporary file after crash | One partial `.720p.mp4.*.tmp` |
| Temporary files after recovery | None |

The first attempt left its partial transcode on the shared PVC because forced
Pod deletion prevented the worker's `finally` cleanup from running. Lease
recovery removed that file before the second attempt processed the video. The
thumbnail storage key was reused idempotently, and only one thumbnail and one
720p output remained registered.

### Interpretation

Kubernetes restored worker capacity much faster than StreamForge restored the
job: the replacement was Ready in 3.784 seconds, but the job remained owned by
the dead worker until its durable lease expired at 31.611 seconds. This is the
correct safety behavior because Kubernetes does not know whether PostgreSQL job
ownership is valid.

The retry began only 19 ms after abandonment in this run and completed the
medium video in approximately 13.82 seconds. With the current 30-second fallback
poll, another run could take up to nearly one additional polling interval to
detect an expired lease.

## Conclusion

Deleting an FFmpeg worker Pod does not lose or duplicate the video. Kubernetes
restores the four-replica Deployment, while the application lease mechanism
independently marks the abandoned attempt, cleans its partial artifact, creates
one retry, and finishes the video as `READY`.

The dominant recovery delay is the 30-second lease, not Pod replacement. Future
work can compare shorter leases or a dedicated expired-lease scanner, but only
after measuring the false-recovery risk during slow processing or transient
database failures.

Raw measurements are stored in [results.json](results.json).
