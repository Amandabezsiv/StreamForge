import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    codec: str
    bitrate: int
    fps: float


def extract_metadata(input_path: Path) -> VideoMetadata:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,codec_name,bit_rate,avg_frame_rate:format=duration,bit_rate",
        "-of",
        "json",
        str(input_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        media_format = payload["format"]
        bitrate = stream.get("bit_rate") or media_format.get("bit_rate")
        frame_rate = stream.get("avg_frame_rate")
        if not bitrate or not frame_rate or frame_rate == "0/0":
            raise KeyError("bitrate or frame rate is unavailable")
        return VideoMetadata(
            duration_seconds=float(media_format["duration"]),
            width=int(stream["width"]),
            height=int(stream["height"]),
            codec=str(stream["codec_name"]),
            bitrate=int(bitrate),
            fps=float(Fraction(frame_rate)),
        )
    except FileNotFoundError as exc:
        raise MediaCommandError("ffprobe is not installed or not in PATH") from exc
    except (subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise MediaCommandError(f"ffprobe failed: {detail.strip()}") from exc
