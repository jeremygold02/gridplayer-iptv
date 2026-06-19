from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

from .config import DATA_DIR
from .recording_types import RecordingError


INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def default_recording_dir() -> Path:
    return Path.home() / "Videos" / "IPTV Multi Player"


def fallback_recording_dir() -> Path:
    return DATA_DIR / "recordings"


def configured_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def effective_recording_dir(settings: dict[str, Any], create: bool = False) -> Path:
    directory = configured_path(settings.get("recording_dir")) or default_recording_dir()
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            directory = fallback_recording_dir()
            directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_filename(value: str, fallback: str = "recording") -> str:
    cleaned = "".join("_" if char in INVALID_FILENAME_CHARS or ord(char) < 32 else char for char in value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:140] or fallback


def recording_path(channel_name: str, settings: dict[str, Any]) -> Path:
    directory = effective_recording_dir(settings, create=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{timestamp} - {sanitize_filename(channel_name)}"
    return directory / f"{base}.ts"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RecordingError("Could not choose a unique output filename.")
