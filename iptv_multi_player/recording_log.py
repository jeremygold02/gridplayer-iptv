from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR


RECORDING_LOG_PATH = DATA_DIR / "recording_session.log"


def recording_log_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDING_LOG_PATH


def reset_recording_session_log() -> None:
    path = recording_log_path()
    timestamp = datetime.now().isoformat(timespec="seconds")
    path.write_text(f"[{timestamp}] IPTV Multi Player recording session started\n", encoding="utf-8")


def append_recording_log(title: str, lines: list[Any] | None = None) -> None:
    try:
        path = recording_log_path()
        timestamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"\n[{timestamp}] {title}\n")
            for line in lines or []:
                handle.write(f"{line}\n")
    except OSError:
        pass
