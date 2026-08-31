from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "StreamForge"
    app_version: str = "0.1.0"
    database_url: str = (
        "postgresql+psycopg://streamforge:streamforge@localhost:5432/streamforge"
    )
    storage_path: Path = Path("storage")
    max_upload_size_bytes: int = 1024 * 1024 * 1024
    ffmpeg_threads: int = 0
    job_lease_seconds: float = 30.0
    job_lease_renewal_seconds: float = 10.0
    job_notifications_enabled: bool = True
    diagnostic_publish_commit_delay_seconds: float = 0.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
