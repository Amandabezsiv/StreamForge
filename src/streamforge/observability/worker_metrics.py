from prometheus_client import Counter, Histogram

JOBS_COMPLETED = Counter(
    "streamforge_jobs_completed",
    "Processing jobs completed by this worker process.",
)
JOBS_FAILED = Counter(
    "streamforge_jobs_failed",
    "Processing jobs failed by this worker process.",
)
JOB_PICKUP_DURATION = Histogram(
    "streamforge_job_pickup_duration_seconds",
    "Time from durable job creation until atomic worker claim.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
JOB_PROCESSING_DURATION = Histogram(
    "streamforge_job_processing_duration_seconds",
    "Time spent processing a claimed video job.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)
WORKER_LEASE_EXPIRED = Counter(
    "streamforge_worker_lease_expired",
    "Processing attempts abandoned because their worker lease expired.",
)
JOB_RETRIES = Counter(
    "streamforge_job_retries",
    "New processing attempts created after abandoned jobs.",
)
