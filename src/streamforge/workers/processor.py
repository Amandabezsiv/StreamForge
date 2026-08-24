import argparse
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
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

logger = logging.getLogger("streamforge.worker")


def elapsed_seconds(start: datetime, end: datetime) -> float:
    """Return elapsed wall time while tolerating timezone-naive test databases."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0.0, (end - start).total_seconds())


def record_event(
    db: Session, job: ProcessingJob, event_type: str, message: str | None = None
) -> None:
    db.add(
        ProcessingEvent(
            video_id=job.video_id,
            job_id=job.id,
            event_type=event_type,
            message=message,
        )
    )


def acquire_pending_job(db: Session) -> uuid.UUID | None:
    """Atomically claim the oldest pending job without blocking other workers."""
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

    now = datetime.now(timezone.utc)
    job.status = JobStatus.PROCESSING
    job.started_at = now
    job.queue_wait_seconds = elapsed_seconds(job.created_at, now)
    job.video.status = VideoStatus.PROCESSING
    record_event(db, job, "JOB_STARTED")
    job_id = job.id
    db.commit()
    return job_id


def process_job(db: Session, job_id: uuid.UUID, settings: Settings) -> None:
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

    try:
        stage_started = time.perf_counter()
        metadata = extract_metadata(input_path)
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
        generate_thumbnail(input_path, thumbnail_path)
        job.thumbnail_duration_seconds = time.perf_counter() - stage_started
        db.add(
            VideoOutput(
                video_id=video.id,
                type=OutputType.THUMBNAIL,
                resolution=None,
                storage_key=thumbnail_path.relative_to(settings.storage_path).as_posix(),
                size_bytes=thumbnail_path.stat().st_size,
            )
        )
        record_event(db, job, "THUMBNAIL_CREATED")
        db.commit()

        record_event(db, job, "TRANSCODING_STARTED")
        db.commit()
        stage_started = time.perf_counter()
        transcode_720p(input_path, transcoded_path)
        job.transcoding_duration_seconds = time.perf_counter() - stage_started
        db.add(
            VideoOutput(
                video_id=video.id,
                type=OutputType.TRANSCODED_VIDEO,
                resolution="720p",
                storage_key=transcoded_path.relative_to(settings.storage_path).as_posix(),
                size_bytes=transcoded_path.stat().st_size,
            )
        )
        record_event(db, job, "TRANSCODING_COMPLETED")

        job.status = JobStatus.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        job.processing_duration_seconds = time.perf_counter() - processing_started
        job.total_time_to_ready_seconds = elapsed_seconds(
            video.created_at, job.finished_at
        )
        video.status = VideoStatus.READY
        record_event(db, job, "JOB_COMPLETED")
        db.commit()
        logger.info("video processing completed", extra={"video_id": str(video.id), "job_id": str(job.id)})
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.processing_duration_seconds = time.perf_counter() - processing_started
            job.error_code = type(exc).__name__
            job.error_message = str(exc)[:4000]
            job.video.status = VideoStatus.FAILED
            record_event(db, job, "JOB_FAILED", str(exc)[:4000])
            db.commit()
        logger.exception("video processing failed", extra={"job_id": str(job_id)})


def run_worker(once: bool = False, poll_interval: float = 2.0) -> None:
    settings = get_settings()
    logger.info("worker started")
    while True:
        with SessionLocal() as db:
            job_id = acquire_pending_job(db)
            if job_id is not None:
                process_job(db, job_id, settings)
        if once:
            return
        if job_id is None:
            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process pending StreamForge videos")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run_worker(once=args.once, poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        logger.info("worker stopped")
