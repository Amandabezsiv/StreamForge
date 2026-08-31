from pathlib import Path

import pytest

from streamforge.media import ffmpeg
from streamforge.media.ffprobe import MediaCommandError


def test_transcode_publishes_completed_temporary_file_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "original.mp4"
    output_path = tmp_path / "720p.mp4"
    input_path.write_bytes(b"original")

    def successful_run(command: list[str], _operation: str) -> None:
        temporary_path = Path(command[-1])
        assert temporary_path.parent == output_path.parent
        assert temporary_path.name.startswith(".720p.mp4.")
        assert temporary_path.suffix == ".tmp"
        assert command[-3:-1] == ["-f", "mp4"]
        assert not output_path.exists()
        temporary_path.write_bytes(b"complete-transcode")

    monkeypatch.setattr(ffmpeg, "_run_ffmpeg", successful_run)

    ffmpeg.transcode_720p(input_path, output_path, threads=3)

    assert output_path.read_bytes() == b"complete-transcode"
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_transcode_preserves_existing_output_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "original.mp4"
    output_path = tmp_path / "720p.mp4"
    input_path.write_bytes(b"original")
    output_path.write_bytes(b"previous-complete-output")

    def failed_run(command: list[str], _operation: str) -> None:
        Path(command[-1]).write_bytes(b"partial")
        raise MediaCommandError("injected FFmpeg failure")

    monkeypatch.setattr(ffmpeg, "_run_ffmpeg", failed_run)

    with pytest.raises(MediaCommandError, match="injected FFmpeg failure"):
        ffmpeg.transcode_720p(input_path, output_path)

    assert output_path.read_bytes() == b"previous-complete-output"
    assert list(tmp_path.glob("*.tmp")) == []


def test_lost_lease_prevents_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "original.mp4"
    output_path = tmp_path / "720p.mp4"
    input_path.write_bytes(b"original")
    output_path.write_bytes(b"previous-complete-output")

    def successful_run(command: list[str], _operation: str) -> None:
        Path(command[-1]).write_bytes(b"new-complete-output")

    def reject_publication() -> None:
        raise RuntimeError("lease ownership lost")

    monkeypatch.setattr(ffmpeg, "_run_ffmpeg", successful_run)

    with pytest.raises(RuntimeError, match="lease ownership lost"):
        ffmpeg.transcode_720p(
            input_path, output_path, verify_before_publish=reject_publication
        )

    assert output_path.read_bytes() == b"previous-complete-output"
    assert list(tmp_path.glob("*.tmp")) == []
