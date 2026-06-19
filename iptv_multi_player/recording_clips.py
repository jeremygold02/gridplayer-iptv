from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .config import DATA_DIR
from .recording_ffmpeg import command_text, run_hidden_kwargs
from .recording_log import append_recording_log
from .recording_paths import effective_recording_dir, sanitize_filename, unique_path
from .recording_types import RecordingError


CLIP_DURATION_PRESETS = (
    {"seconds": 30, "label": "30 seconds"},
    {"seconds": 60, "label": "1 minute"},
    {"seconds": 120, "label": "2 minutes"},
    {"seconds": 300, "label": "5 minutes"},
    {"seconds": 600, "label": "10 minutes"},
)
CLIP_DURATION_SECONDS = {item["seconds"] for item in CLIP_DURATION_PRESETS}
DEFAULT_CLIP_SECONDS = 60
CLIP_SEGMENT_SECONDS = 2


def sanitize_clip_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CLIP_SECONDS
    return seconds if seconds in CLIP_DURATION_SECONDS else DEFAULT_CLIP_SECONDS


def clip_path(channel_name: str, settings: dict[str, Any]) -> Path:
    directory = effective_recording_dir(settings, create=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{timestamp} - {sanitize_filename(channel_name)} Clip"
    return unique_path(directory / f"{base}.ts")


def clip_buffer_root() -> Path:
    return DATA_DIR / "clip_buffer"


def reset_clip_buffer_root() -> Path:
    root = clip_buffer_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def clip_segment_count(clip_seconds: int) -> int:
    return max(3, (clip_seconds + CLIP_SEGMENT_SECONDS - 1) // CLIP_SEGMENT_SECONDS + 3)


def clip_buffer_dir(channel_name: str) -> Path:
    root = reset_clip_buffer_root()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    directory = root / f"{timestamp} - {sanitize_filename(channel_name, 'clip')}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def clip_segment_files(buffer_dir: Path | None) -> list[Path]:
    if not buffer_dir or not buffer_dir.is_dir():
        return []
    files: list[tuple[float, str, Path]] = []
    for path in buffer_dir.glob("buffer_*.ts"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > 0:
            files.append((stat.st_mtime, path.name, path))
    return [path for _, _, path in sorted(files)]


def cleanup_clip_buffer(active: Any) -> None:
    buffer_dir = getattr(active, "buffer_dir", None)
    if buffer_dir and buffer_dir.exists():
        shutil.rmtree(buffer_dir, ignore_errors=True)


def concat_file_line(path: Path) -> str:
    escaped_path = path.as_posix().replace("\\", "/").replace("'", "'\\''")
    return f"file '{escaped_path}'"


def write_concat_file(path: Path, segments: list[Path]) -> None:
    lines = [concat_file_line(segment) for segment in segments]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remux_clip_segments(ffmpeg_path: str, segments: list[Path], output_path: Path) -> None:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    list_path = output_path.with_suffix(output_path.suffix + ".concat.txt")
    try:
        write_concat_file(list_path, segments)
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+genpts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-map",
            "0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-mpegts_flags",
            "+resend_headers",
            "-f",
            "mpegts",
            str(temp_path),
        ]
        append_recording_log("clip remux start", [command_text(command)])
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            **run_hidden_kwargs(),
        )
        append_recording_log(
            "clip remux finished",
            [
                f"returncode={result.returncode}",
                "stdout:",
                result.stdout or "",
                "stderr:",
                result.stderr or "",
            ],
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Could not save clip.").strip()
            raise RecordingError(message)
        temp_path.replace(output_path)
    except subprocess.TimeoutExpired as exc:
        raise RecordingError("Could not save clip before the timeout.") from exc
    except OSError as exc:
        raise RecordingError(f"Could not save clip: {exc}") from exc
    finally:
        for transient_path in (temp_path, list_path):
            try:
                transient_path.unlink(missing_ok=True)
            except OSError:
                pass
