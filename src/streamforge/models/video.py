import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from streamforge.core.database import Base
from streamforge.models.types import VideoStatus

if TYPE_CHECKING:
    from streamforge.models.processing_job import ProcessingJob
    from streamforge.models.video_output import VideoOutput


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[VideoStatus] = mapped_column(
        String(20), default=VideoStatus.UPLOADED, index=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(50))
    bitrate: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="video")
    outputs: Mapped[list["VideoOutput"]] = relationship(back_populates="video")
