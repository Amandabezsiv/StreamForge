import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from streamforge.api.app import app
from streamforge.core.config import Settings, get_settings
from streamforge.core.database import Base, get_db
from streamforge.models import ProcessingEvent, ProcessingJob, Video, VideoOutput
from streamforge.models.types import JobStatus, OutputType
from streamforge.workers import processor


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def override_get_db():
    with TestingSession() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def create_video(session: Session) -> Video:
    video = Video(
        original_filename="example.mp4",
        storage_key=f"videos/{uuid.uuid4()}/original.mp4",
        size_bytes=1024,
    )
    session.add(video)
    session.commit()
    return video


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_unknown_video_returns_404() -> None:
    response = client.get(f"/api/v1/videos/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Video not found"}


def test_get_video_and_related_resources() -> None:
    with TestingSession() as session:
        video = create_video(session)
        session.add_all(
            [
                ProcessingJob(video_id=video.id, status=JobStatus.PENDING),
                VideoOutput(
                    video_id=video.id,
                    type=OutputType.THUMBNAIL,
                    storage_key=f"videos/{video.id}/thumbnail.jpg",
                    size_bytes=512,
                ),
            ]
        )
        session.commit()
        video_id = video.id

    video_response = client.get(f"/api/v1/videos/{video_id}")
    jobs_response = client.get(f"/api/v1/videos/{video_id}/jobs")
    outputs_response = client.get(f"/api/v1/videos/{video_id}/outputs")

    assert video_response.status_code == 200
    assert video_response.json()["status"] == "UPLOADED"
    assert jobs_response.status_code == 200
    assert jobs_response.json()[0]["status"] == "PENDING"
    assert outputs_response.status_code == 200
    assert outputs_response.json()[0]["type"] == "THUMBNAIL"


def test_upload_video_stores_file_and_creates_pending_job(tmp_path) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(storage_path=tmp_path)
    try:
        response = client.post(
            "/api/v1/videos",
            files={"file": ("sample.mp4", b"fake-video-content", "video/mp4")},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "UPLOADED"

    video_id = uuid.UUID(payload["video_id"])
    stored_file = tmp_path / "videos" / str(video_id) / "original.mp4"
    assert stored_file.read_bytes() == b"fake-video-content"

    with TestingSession() as session:
        video = session.get(Video, video_id)
        job = session.query(ProcessingJob).filter_by(video_id=video_id).one()
        event = session.query(ProcessingEvent).filter_by(video_id=video_id).one()

        assert video is not None
        assert video.size_bytes == len(b"fake-video-content")
        assert job.status == JobStatus.PENDING
        assert job.attempt == 1
        assert event.job_id == job.id
        assert event.event_type == "JOB_CREATED"


def test_upload_rejects_unsupported_format(tmp_path) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(storage_path=tmp_path)
    try:
        response = client.post(
            "/api/v1/videos",
            files={"file": ("sample.txt", b"not-a-video", "text/plain")},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 415
    assert not (tmp_path / "videos").exists()

    with TestingSession() as session:
        assert session.query(Video).count() == 0


def test_upload_removes_partial_file_when_size_limit_is_exceeded(tmp_path) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        storage_path=tmp_path, max_upload_size_bytes=4
    )
    try:
        response = client.post(
            "/api/v1/videos",
            files={"file": ("large.mkv", b"12345", "video/x-matroska")},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 413
    assert list(tmp_path.rglob("*")) == [tmp_path / "videos"]

    with TestingSession() as session:
        assert session.query(Video).count() == 0


def test_worker_extracts_metadata_and_creates_required_outputs(
    tmp_path, monkeypatch
) -> None:
    video_id = uuid.uuid4()
    video_directory = tmp_path / "videos" / str(video_id)
    video_directory.mkdir(parents=True)
    (video_directory / "original.mp4").write_bytes(b"original")

    def fake_thumbnail(_input, output) -> None:
        output.write_bytes(b"thumbnail")

    def fake_transcode(_input, output) -> None:
        output.write_bytes(b"transcoded")

    monkeypatch.setattr(
        processor,
        "extract_metadata",
        lambda _path: SimpleNamespace(
            duration_seconds=12.5,
            width=1920,
            height=1080,
            codec="h264",
            bitrate=2_000_000,
            fps=30.0,
        ),
    )
    monkeypatch.setattr(processor, "generate_thumbnail", fake_thumbnail)
    monkeypatch.setattr(processor, "transcode_720p", fake_transcode)

    with TestingSession() as session:
        video = Video(
            id=video_id,
            original_filename="original.mp4",
            storage_key=f"videos/{video_id}/original.mp4",
            size_bytes=8,
        )
        job = ProcessingJob(video_id=video_id)
        session.add_all([video, job])
        session.commit()

        job_id = processor.acquire_pending_job(session)
        assert job_id == job.id
        processor.process_job(session, job_id, Settings(storage_path=tmp_path))

        session.refresh(video)
        session.refresh(job)
        outputs = session.query(VideoOutput).filter_by(video_id=video_id).all()
        events = session.query(ProcessingEvent).filter_by(video_id=video_id).all()

        assert video.status == "READY"
        assert video.duration_seconds == 12.5
        assert video.width == 1920
        assert video.height == 1080
        assert video.codec == "h264"
        assert video.bitrate == 2_000_000
        assert video.fps == 30.0
        assert job.status == JobStatus.COMPLETED
        assert job.started_at is not None
        assert job.finished_at is not None
        assert job.queue_wait_seconds is not None
        assert job.processing_duration_seconds is not None
        assert job.metadata_duration_seconds is not None
        assert job.thumbnail_duration_seconds is not None
        assert job.transcoding_duration_seconds is not None
        assert job.total_time_to_ready_seconds is not None
        assert {output.type for output in outputs} == {
            OutputType.THUMBNAIL,
            OutputType.TRANSCODED_VIDEO,
        }
        assert {event.event_type for event in events} >= {
            "JOB_STARTED",
            "METADATA_EXTRACTED",
            "THUMBNAIL_CREATED",
            "TRANSCODING_STARTED",
            "TRANSCODING_COMPLETED",
            "JOB_COMPLETED",
        }


def test_worker_registers_processing_failure(tmp_path, monkeypatch) -> None:
    video_id = uuid.uuid4()
    video_directory = tmp_path / "videos" / str(video_id)
    video_directory.mkdir(parents=True)
    (video_directory / "original.mp4").write_bytes(b"invalid")

    monkeypatch.setattr(
        processor,
        "extract_metadata",
        lambda _path: (_ for _ in ()).throw(RuntimeError("ffprobe failed")),
    )

    with TestingSession() as session:
        video = Video(
            id=video_id,
            original_filename="original.mp4",
            storage_key=f"videos/{video_id}/original.mp4",
            size_bytes=7,
        )
        job = ProcessingJob(video_id=video_id)
        session.add_all([video, job])
        session.commit()

        job_id = processor.acquire_pending_job(session)
        processor.process_job(session, job_id, Settings(storage_path=tmp_path))

        session.refresh(video)
        session.refresh(job)
        events = session.query(ProcessingEvent).filter_by(job_id=job.id).all()

        assert video.status == "FAILED"
        assert job.status == JobStatus.FAILED
        assert job.finished_at is not None
        assert job.processing_duration_seconds is not None
        assert job.error_code == "RuntimeError"
        assert job.error_message == "ffprobe failed"
        assert "JOB_FAILED" in {event.event_type for event in events}
