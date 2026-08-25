import subprocess
from pathlib import Path

from streamforge.media.ffprobe import MediaCommandError


def _run_ffmpeg(command: list[str], operation: str) -> None:
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise MediaCommandError("ffmpeg is not installed or not in PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaCommandError(f"{operation} failed: {exc.stderr.strip()}") from exc


def generate_thumbnail(input_path: Path, output_path: Path, threads: int = 0) -> None:
    _run_ffmpeg(
        [
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
            str(output_path),
        ],
        "thumbnail generation",
    )


def transcode_720p(input_path: Path, output_path: Path, threads: int = 0) -> None:
    _run_ffmpeg(
        [
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
            str(output_path),
        ],
        "720p transcoding",
    )
