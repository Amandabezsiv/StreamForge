import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from streamforge.core.database import Base
from streamforge.models.types import JobStatus, JobType

if TYPE_CHECKING:
    from streamforge.models.processing_event import ProcessingEvent
    from streamforge.models.video import Video


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[JobType] = mapped_column(String(30), default=JobType.PROCESS_VIDEO)
    status: Mapped[JobStatus] = mapped_column(
        String(20), default=JobStatus.PENDING, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    queue_wait_seconds: Mapped[float | None] = mapped_column(Float)
    processing_duration_seconds: Mapped[float | None] = mapped_column(Float)
    metadata_duration_seconds: Mapped[float | None] = mapped_column(Float)
    thumbnail_duration_seconds: Mapped[float | None] = mapped_column(Float)
    transcoding_duration_seconds: Mapped[float | None] = mapped_column(Float)
    total_time_to_ready_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    video: Mapped["Video"] = relationship(back_populates="jobs")
    events: Mapped[list["ProcessingEvent"]] = relationship(back_populates="job")
