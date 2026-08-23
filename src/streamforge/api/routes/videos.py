import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from streamforge.core.config import Settings, get_settings
from streamforge.core.database import get_db
from streamforge.models.processing_event import ProcessingEvent
from streamforge.models.processing_job import ProcessingJob
from streamforge.models.video import Video
from streamforge.models.video_output import VideoOutput
from streamforge.models.types import JobStatus, VideoStatus
from streamforge.schemas.video import (
    ProcessingJobResponse,
    VideoOutputResponse,
    VideoResponse,
    VideoUploadResponse,
)

router = APIRouter(prefix="/videos", tags=["videos"])
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


def get_video_or_404(video_id: uuid.UUID, db: Session) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Video not found"
        )
    return video


@router.post(
    "",
    response_model=VideoUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VideoUploadResponse:
    """Store an original video and enqueue its first processing job."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required")
    if len(file.filename) > 255:
        raise HTTPException(status_code=400, detail="Filename is too long")

    extension = Path(file.filename).suffix.lower()
    if extension not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported video format. Supported formats: {supported}",
        )

    video_id = uuid.uuid4()
    relative_storage_key = Path("videos") / str(video_id) / f"original{extension}"
    video_directory = settings.storage_path / relative_storage_key.parent
    destination = settings.storage_path / relative_storage_key
    size_bytes = 0

    try:
        video_directory.mkdir(parents=True, exist_ok=False)
        with destination.open("xb") as stored_file:
            while chunk := file.file.read(UPLOAD_CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Uploaded video exceeds the configured size limit",
                    )
                stored_file.write(chunk)

        if size_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded video is empty")

        video = Video(
            id=video_id,
            original_filename=file.filename,
            storage_key=relative_storage_key.as_posix(),
            size_bytes=size_bytes,
            status=VideoStatus.UPLOADED,
        )
        job = ProcessingJob(
            video_id=video_id,
            status=JobStatus.PENDING,
            attempt=1,
        )
        event = ProcessingEvent(
            video_id=video_id,
            job=job,
            event_type="JOB_CREATED",
            message="Initial processing job created after video upload",
        )
        db.add_all([video, job, event])
        db.commit()

        return VideoUploadResponse(video_id=video.id, status=video.status)
    except HTTPException:
        db.rollback()
        shutil.rmtree(video_directory, ignore_errors=True)
        raise
    except Exception as exc:
        db.rollback()
        shutil.rmtree(video_directory, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Video upload could not be completed",
        ) from exc
    finally:
        file.file.close()


@router.get("/{video_id}", response_model=VideoResponse)
def get_video(video_id: uuid.UUID, db: Session = Depends(get_db)) -> Video:
    return get_video_or_404(video_id, db)


@router.get("/{video_id}/outputs", response_model=list[VideoOutputResponse])
def get_video_outputs(
    video_id: uuid.UUID, db: Session = Depends(get_db)
) -> list[VideoOutput]:
    get_video_or_404(video_id, db)
    return list(
        db.scalars(
            select(VideoOutput)
            .where(VideoOutput.video_id == video_id)
            .order_by(VideoOutput.created_at)
        )
    )


@router.get("/{video_id}/jobs", response_model=list[ProcessingJobResponse])
def get_video_jobs(
    video_id: uuid.UUID, db: Session = Depends(get_db)
) -> list[ProcessingJob]:
    get_video_or_404(video_id, db)
    return list(
        db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id)
            .order_by(ProcessingJob.created_at)
        )
    )
