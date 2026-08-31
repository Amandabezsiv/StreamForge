import argparse
import logging
import socket
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from streamforge.core.config import Settings, get_settings
from streamforge.core.database import SessionLocal
from streamforge.media.ffmpeg import generate_thumbnail, transcode_720p
from streamforge.media.ffprobe import extract_metadata
from streamforge.models.processing_event import ProcessingEvent
from streamforge.models.processing_job import ProcessingJob
from streamforge.models.types import JobStatus, OutputType, VideoStatus
from streamforge.models.video import Video
from streamforge.models.video_output import VideoOutput
from streamforge.workers.notifications import JobNotificationListener

logger = logging.getLogger("streamforge.worker")


class LeaseOwnershipLostError(RuntimeError):
    pass


def elapsed_seconds(start: datetime, end: datetime) -> float:
    """Return elapsed wall time while tolerating timezone-naive test databases."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0.0, (end - start).total_seconds())


def record_event(
    db: Session, job: ProcessingJob, event_type: str, message: str | None = None
) -> None:
    db.add(
        ProcessingEvent(
            video_id=job.video_id,
            job=job,
            event_type=event_type,
            message=message,
        )
    )


def acquire_pending_job(
    db: Session,
    *,
    worker_id: str = "worker",
    lease_seconds: float = 30.0,
    diagnostic_lock_hold_seconds: float = 0.0,
) -> uuid.UUID | None:
    """Atomically claim the oldest pending job without blocking other workers.

    The optional delay makes lock contention observable in diagnostics. Normal
    workers leave it at zero.
    """
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.status == JobStatus.PENDING)
        .order_by(ProcessingJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.rollback()
        return None

    now = datetime.now(UTC)
    job.status = JobStatus.PROCESSING
    job.claimed_by = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.started_at = now
    job.queue_wait_seconds = elapsed_seconds(job.created_at, now)
    job.video.status = VideoStatus.PROCESSING
    record_event(db, job, "JOB_STARTED")
    job_id = job.id
    if diagnostic_lock_hold_seconds > 0:
        time.sleep(diagnostic_lock_hold_seconds)
    db.commit()
    return job_id


def renew_job_lease(job_id: uuid.UUID, worker_id: str, lease_seconds: float) -> bool:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        result = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status == JobStatus.PROCESSING,
                ProcessingJob.claimed_by == worker_id,
                ProcessingJob.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        )
        db.commit()
        return result.rowcount == 1


def lock_owned_job(
    db: Session, job_id: uuid.UUID, worker_id: str, lease_seconds: float
) -> ProcessingJob:
    now = datetime.now(UTC)
    job = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JobStatus.PROCESSING,
            ProcessingJob.claimed_by == worker_id,
            ProcessingJob.lease_expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        db.rollback()
        raise LeaseOwnershipLostError(
            f"Worker {worker_id} no longer owns processing job {job_id}"
        )
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return job


class LeaseHeartbeat:
    def __init__(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        lease_seconds: float,
        renewal_seconds: float,
    ) -> None:
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.renewal_seconds = renewal_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    @property
    def ownership_lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self.renewal_seconds):
            try:
                if not renew_job_lease(self.job_id, self.worker_id, self.lease_seconds):
                    self._lost.set()
                    return
            except (OSError, SQLAlchemyError):
                logger.exception(
                    "job lease renewal failed", extra={"job_id": str(self.job_id)}
                )


def cleanup_temporary_artifacts(video_directory: Path) -> list[str]:
    removed = []
    if not video_directory.is_dir():
        return removed
    for path in video_directory.glob(".*.tmp"):
        path.unlink(missing_ok=True)
        removed.append(str(path))
    return removed


def recover_expired_jobs(db: Session, settings: Settings, limit: int = 10) -> int:
    now = datetime.now(UTC)
    expired_jobs = list(
        db.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == JobStatus.PROCESSING,
                ProcessingJob.lease_expires_at <= now,
            )
            .order_by(ProcessingJob.lease_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    cleanup_directories: list[Path] = []
    for job in expired_jobs:
        previous_owner = job.claimed_by
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.error_code = "WorkerLeaseExpired"
        job.error_message = f"Lease expired while owned by {previous_owner}"
        job.claimed_by = None
        job.lease_expires_at = None
        record_event(db, job, "JOB_ABANDONED", job.error_message)

        retry = ProcessingJob(
            video_id=job.video_id,
            status=JobStatus.PENDING,
            attempt=job.attempt + 1,
        )
        db.add(retry)
        record_event(
            db,
            retry,
            "JOB_CREATED",
            f"Recovery attempt created after abandoned job {job.id}",
        )
        job.video.status = VideoStatus.UPLOADED
        cleanup_directories.append(
            (settings.storage_path / job.video.storage_key).parent
        )
    db.commit()

    for directory in cleanup_directories:
        removed = cleanup_temporary_artifacts(directory)
        if removed:
            logger.info("removed abandoned temporary outputs", extra={"files": removed})
    return len(expired_jobs)


def register_output(db: Session, output: VideoOutput) -> None:
    existing = db.scalar(
        select(VideoOutput).where(VideoOutput.storage_key == output.storage_key)
    )
    if existing is None:
        db.add(output)
    else:
        existing.size_bytes = output.size_bytes
        existing.type = output.type
        existing.resolution = output.resolution


def process_job(
    db: Session,
    job_id: uuid.UUID,
    settings: Settings,
    worker_id: str | None = None,
) -> None:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise RuntimeError(f"Processing job {job_id} no longer exists")
    video = db.get(Video, job.video_id)
    if video is None:
        raise RuntimeError(f"Video {job.video_id} no longer exists")

    input_path = settings.storage_path / video.storage_key
    video_directory = input_path.parent
    thumbnail_path = video_directory / "thumbnail.jpg"
    transcoded_path = video_directory / "720p.mp4"
    processing_started = time.perf_counter()
    heartbeat = None
    if worker_id is not None:
        heartbeat = LeaseHeartbeat(
            job_id,
            worker_id,
            settings.job_lease_seconds,
            settings.job_lease_renewal_seconds,
        )
        heartbeat.start()

    def verify_ownership() -> None:
        if worker_id is not None:
            lock_owned_job(db, job_id, worker_id, settings.job_lease_seconds)

    try:
        stage_started = time.perf_counter()
        metadata = extract_metadata(input_path)
        verify_ownership()
        job.metadata_duration_seconds = time.perf_counter() - stage_started
        video.duration_seconds = metadata.duration_seconds
        video.width = metadata.width
        video.height = metadata.height
        video.codec = metadata.codec
        video.bitrate = metadata.bitrate
        video.fps = metadata.fps
        record_event(db, job, "METADATA_EXTRACTED")
        db.commit()

        stage_started = time.perf_counter()
        generate_thumbnail(
            input_path,
            thumbnail_path,
            settings.ffmpeg_threads,
            verify_ownership,
        )
        job.thumbnail_duration_seconds = time.perf_counter() - stage_started
        register_output(
            db,
            VideoOutput(
                video_id=video.id,
                type=OutputType.THUMBNAIL,
                resolution=None,
                storage_key=thumbnail_path.relative_to(
                    settings.storage_path
                ).as_posix(),
                size_bytes=thumbnail_path.stat().st_size,
            ),
        )
        record_event(db, job, "THUMBNAIL_CREATED")
        db.commit()

        record_event(db, job, "TRANSCODING_STARTED")
        db.commit()
        stage_started = time.perf_counter()
        transcode_720p(
            input_path,
            transcoded_path,
            settings.ffmpeg_threads,
            verify_ownership,
        )
        if settings.diagnostic_publish_commit_delay_seconds > 0:
            logger.warning(
                "diagnostic pause after output publication",
                extra={"job_id": str(job_id)},
            )
            time.sleep(settings.diagnostic_publish_commit_delay_seconds)
        job.transcoding_duration_seconds = time.perf_counter() - stage_started
        register_output(
            db,
            VideoOutput(
                video_id=video.id,
                type=OutputType.TRANSCODED_VIDEO,
                resolution="720p",
                storage_key=transcoded_path.relative_to(
                    settings.storage_path
                ).as_posix(),
                size_bytes=transcoded_path.stat().st_size,
            ),
        )
        record_event(db, job, "TRANSCODING_COMPLETED")

        job.status = JobStatus.COMPLETED
        job.claimed_by = None
        job.lease_expires_at = None
        job.finished_at = datetime.now(UTC)
        job.processing_duration_seconds = time.perf_counter() - processing_started
        job.total_time_to_ready_seconds = elapsed_seconds(
            video.created_at, job.finished_at
        )
        video.status = VideoStatus.READY
        record_event(db, job, "JOB_COMPLETED")
        db.commit()
        logger.info(
            "video processing completed",
            extra={"video_id": str(video.id), "job_id": str(job.id)},
        )
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        owns_job = worker_id is None
        if worker_id is not None and job is not None:
            try:
                job = lock_owned_job(db, job_id, worker_id, settings.job_lease_seconds)
                owns_job = True
            except LeaseOwnershipLostError:
                owns_job = False
        if job is not None and owns_job:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(UTC)
            job.processing_duration_seconds = time.perf_counter() - processing_started
            job.error_code = type(exc).__name__
            job.error_message = str(exc)[:4000]
            job.claimed_by = None
            job.lease_expires_at = None
            job.video.status = VideoStatus.FAILED
            record_event(db, job, "JOB_FAILED", str(exc)[:4000])
            db.commit()
        logger.exception("video processing failed", extra={"job_id": str(job_id)})
    finally:
        if heartbeat is not None:
            heartbeat.stop()


def wait_for_new_job(
    listener: JobNotificationListener | None, poll_interval: float
) -> bool:
    if listener is None:
        time.sleep(poll_interval)
        return False
    return listener.wait(poll_interval)


def run_worker(once: bool = False, poll_interval: float = 30.0) -> None:
    settings = get_settings()
    if settings.job_lease_renewal_seconds >= settings.job_lease_seconds:
        raise ValueError(
            "JOB_LEASE_RENEWAL_SECONDS must be less than JOB_LEASE_SECONDS"
        )
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    listener = (
        JobNotificationListener(settings.database_url)
        if settings.job_notifications_enabled and not once
        else None
    )
    if listener is not None:
        listener.start()
    logger.info("worker started", extra={"worker_id": worker_id})
    try:
        while True:
            with SessionLocal() as db:
                recover_expired_jobs(db, settings)
                job_id = acquire_pending_job(
                    db,
                    worker_id=worker_id,
                    lease_seconds=settings.job_lease_seconds,
                )
                if job_id is not None:
                    process_job(db, job_id, settings, worker_id)
            if once:
                return
            if job_id is None:
                wait_for_new_job(listener, poll_interval)
    finally:
        if listener is not None:
            listener.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process pending StreamForge videos")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--poll-interval", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run_worker(once=args.once, poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        logger.info("worker stopped")
