# Experiment 008: Atomic Output Publication

## Objective

Repeat the worker-crash scenario from Experiment 007 after changing FFmpeg
outputs to use temporary files and atomic rename.

## Publication sequence

```text
FFmpeg writes same-directory .tmp file
                    ↓
FFmpeg exits successfully
                    ↓
atomic rename to 720p.mp4
                    ↓
register VideoOutput in PostgreSQL
```

The temporary file and final file are in the same directory and filesystem,
which is required for an atomic rename. FFmpeg receives an explicit output
format because the temporary filename ends in `.tmp`.

The runner resets the Docker Compose network before connecting, preventing
stale container routes from making Psycopg hang during setup. `docker compose
down` does not use `--volumes`, so the PostgreSQL named volume and its data are
preserved. The API and PostgreSQL are started and health-checked before the
runner opens its first database session.

## Expected crash behavior

If the worker receives `SIGKILL` during FFmpeg execution:

- a partial `.tmp` file may remain;
- the final `720p.mp4` must not appear;
- no transcoded `VideoOutput` must be registered;
- the earlier metadata and thumbnail commits remain durable;
- the job still remains `PROCESSING` until lease-based recovery is added.

## Run

```bash
docker compose build worker
uv run python scripts/benchmark_worker_failure.py \
  --experiment-name 008-atomic-output-publication \
  --output experiments/008-atomic-output-publication/results.json
```

## Result

Recorded on 2026-08-26 local time (`2026-08-27T00:17:28Z`):

| Observation | Result |
| --- | --- |
| Video status | `PROCESSING` |
| Job status | `PROCESSING` |
| Metadata | complete |
| Registered outputs | thumbnail only |
| Final `720p.mp4` | **absent** |
| Temporary transcode | present, 0 bytes, invalid |
| Transcoded `VideoOutput` | absent |

The remaining partial artifact was named:

```text
.720p.mp4.b96f9413f8a44c048b2767452ab9bb67.tmp
```

It was never visible at the final `720p.mp4` path. PostgreSQL contained no
transcoded output registration because the atomic rename and registration
steps were never reached.

### Comparison with Experiment 007

| Crash outcome | Direct write | Atomic publication |
| --- | --- | --- |
| Invalid partial file | `720p.mp4` | hidden unique `.tmp` |
| Final path exists | yes, invalid | no |
| Transcoded output registered | no | no |
| Job after crash | `PROCESSING` | `PROCESSING` |

## Conclusion

Atomic publication solved the partial-final-file problem. Consumers can now
treat the presence of `720p.mp4` as evidence that FFmpeg completed and the
rename occurred. A hard crash can still leave temporary files, so the planned
lease-recovery process should delete stale `.tmp` artifacts when it reclaims an
abandoned `PROCESSING` job.

There is still a smaller consistency window between atomic rename and the
database commit: a crash there could leave a complete final file without a
`VideoOutput` row. Recovery should handle this idempotently by validating and
registering the complete file or replacing it during retry.
