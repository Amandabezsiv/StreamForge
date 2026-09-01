from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from streamforge.models.processing_job import ProcessingJob
from streamforge.models.types import JobStatus

JOBS_PENDING = Gauge(
    "streamforge_jobs_pending",
    "Current number of pending processing jobs in PostgreSQL.",
)
JOBS_PROCESSING = Gauge(
    "streamforge_jobs_processing",
    "Current number of processing jobs in PostgreSQL.",
)


def render_api_metrics(db: Session) -> tuple[bytes, str]:
    counts = dict(
        db.execute(
            select(ProcessingJob.status, func.count(ProcessingJob.id)).group_by(
                ProcessingJob.status
            )
        ).all()
    )
    JOBS_PENDING.set(counts.get(JobStatus.PENDING, 0))
    JOBS_PROCESSING.set(counts.get(JobStatus.PROCESSING, 0))
    return generate_latest(), CONTENT_TYPE_LATEST
