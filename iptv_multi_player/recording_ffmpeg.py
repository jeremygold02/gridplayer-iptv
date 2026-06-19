from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .recording_paths import configured_path
from .recording_types import FfmpegInfo, FfmpegInstallError


FFMPEG_WINGET_ID = "Gyan.FFmpeg"


def command_text(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def run_hidden_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def candidate_ffmpeg_paths(settings: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    configured = configured_path(settings.get("ffmpeg_path"))
    if configured:
        candidates.append(configured)

    candidates.extend([
        Path(r"C:\ffmpeg\ffmpeg.exe"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
    ])

    package_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if package_root.is_dir():
        candidates.extend(package_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
        candidates.extend(package_root.glob("Gyan.FFmpeg*/ffmpeg*/bin/ffmpeg.exe"))
        candidates.extend(package_root.glob("Gyan.FFmpeg*/bin/ffmpeg.exe"))
        for package_dir in package_root.glob("Gyan.FFmpeg*"):
            if package_dir.is_dir():
                candidates.extend(package_dir.rglob("ffmpeg.exe"))

    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not str(candidate):
            continue
        key = str(candidate).lower()
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def ffprobe_from_ffmpeg(ffmpeg_path: Path) -> Path | None:
    sibling = ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return sibling
    found = shutil.which("ffprobe")
    return Path(found) if found else None


def find_ffmpeg(settings: dict[str, Any]) -> FfmpegInfo:
    ffmpeg_without_probe = ""
    for candidate in candidate_ffmpeg_paths(settings):
        if not candidate.is_file():
            continue
        ffprobe = ffprobe_from_ffmpeg(candidate)
        if ffprobe and ffprobe.is_file():
            return FfmpegInfo(
                available=True,
                ffmpeg_path=str(candidate),
                ffprobe_path=str(ffprobe),
                message="FFmpeg is ready.",
            )
        ffmpeg_without_probe = str(candidate)

    if ffmpeg_without_probe:
        return FfmpegInfo(
            available=False,
            ffmpeg_path=ffmpeg_without_probe,
            message="FFmpeg was found, but ffprobe was not found beside it or on PATH.",
        )

    return FfmpegInfo(
        available=False,
        message="FFmpeg is not installed or was not found on PATH.",
    )


def public_ffmpeg(settings: dict[str, Any]) -> dict[str, Any]:
    info = find_ffmpeg(settings)
    return {
        "available": info.available,
        "path": info.ffmpeg_path,
        "ffprobe_path": info.ffprobe_path,
        "message": info.message,
        "install_id": FFMPEG_WINGET_ID,
    }


def install_ffmpeg() -> FfmpegInfo:
    winget = shutil.which("winget")
    if not winget:
        raise FfmpegInstallError("winget was not found on this PC.")

    command = [
        winget,
        "install",
        "--id",
        FFMPEG_WINGET_ID,
        "--exact",
        "--source",
        "winget",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=900,
        **run_hidden_kwargs(),
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise FfmpegInstallError(output or "winget could not install FFmpeg.")

    info = find_ffmpeg({})
    if not info.available:
        raise FfmpegInstallError("FFmpeg installed, but the executable could not be found yet. Try restarting the app.")
    return info
