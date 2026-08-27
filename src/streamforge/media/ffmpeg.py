import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from streamforge.media.ffprobe import MediaCommandError


def _run_ffmpeg(command: list[str], operation: str) -> None:
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise MediaCommandError("ffmpeg is not installed or not in PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaCommandError(f"{operation} failed: {exc.stderr.strip()}") from exc


def _publish_atomically(
    output_path: Path,
    operation: str,
    build_command: Callable[[Path], list[str]],
    verify_before_publish: Callable[[], None] | None = None,
) -> None:
    """Write beside the destination and expose it only after FFmpeg succeeds."""
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        _run_ffmpeg(build_command(temporary_path), operation)
        if verify_before_publish is not None:
            verify_before_publish()
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate_thumbnail(
    input_path: Path,
    output_path: Path,
    threads: int = 0,
    verify_before_publish: Callable[[], None] | None = None,
) -> None:
    def command(temporary_path: Path) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            "thumbnail",
            "-frames:v",
            "1",
            "-threads",
            str(threads),
            "-f",
            "image2",
            str(temporary_path),
        ]

    _publish_atomically(
        output_path, "thumbnail generation", command, verify_before_publish
    )


def transcode_720p(
    input_path: Path,
    output_path: Path,
    threads: int = 0,
    verify_before_publish: Callable[[], None] | None = None,
) -> None:
    def command(temporary_path: Path) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            "scale=-2:720",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-threads",
            str(threads),
            "-f",
            "mp4",
            str(temporary_path),
        ]

    _publish_atomically(output_path, "720p transcoding", command, verify_before_publish)
