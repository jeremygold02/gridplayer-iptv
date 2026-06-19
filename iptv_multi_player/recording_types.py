from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any


class RecordingError(RuntimeError):
    pass


class FfmpegMissingError(RecordingError):
    pass


class FfmpegInstallError(RecordingError):
    pass


class QualityUnavailableError(RecordingError):
    pass


@dataclass
class FfmpegInfo:
    available: bool
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    message: str = ""


@dataclass
class QualityOption:
    id: str
    label: str
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    fps: float | None = None
    source_url: str | None = None
    map_args: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "bitrate": self.bitrate,
            "fps": self.fps,
        }


@dataclass
class ActiveRecording:
    channel_id: str
    channel_name: str
    quality_id: str
    quality_label: str
    output_path: Path | None
    process: subprocess.Popen
    output_handle: Any | None
    log_handle: Any
    command: list[str]
    started_at: float
    mode: str = "recording"
    clip_seconds: int = 0
    buffer_dir: Path | None = None
    last_clip_path: Path | None = None
    retry_count: int = 0
    retry_after: float = 0.0
    retry_message: str = ""
    last_returncode: int | None = None
