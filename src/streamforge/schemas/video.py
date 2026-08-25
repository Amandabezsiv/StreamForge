import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from streamforge.models.types import JobStatus, JobType, OutputType, VideoStatus


class VideoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    size_bytes: int
    status: VideoStatus
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    bitrate: int | None
    created_at: datetime
    updated_at: datetime


class VideoUploadResponse(BaseModel):
    video_id: uuid.UUID
    status: VideoStatus


class VideoOutputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: OutputType
    resolution: str | None
    storage_key: str
    size_bytes: int
    created_at: datetime


class ProcessingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    queue_wait_seconds: float | None
    processing_duration_seconds: float | None
    metadata_duration_seconds: float | None
    thumbnail_duration_seconds: float | None
    transcoding_duration_seconds: float | None
    total_time_to_ready_seconds: float | None
    created_at: datetime
    updated_at: datetime
