from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urljoin
import urllib.request

from .config import APP_DIR, HTTP_TIMEOUT_SECONDS
from .players import launch_player, player_label


QUALITY_PRESETS = (
    {"id": "best", "label": "Best", "max_height": None},
    {"id": "2160", "label": "4K or lower", "max_height": 2160},
    {"id": "1440", "label": "1440p or lower", "max_height": 1440},
    {"id": "1080", "label": "1080p or lower", "max_height": 1080},
    {"id": "720", "label": "720p or lower", "max_height": 720},
    {"id": "480", "label": "480p or lower", "max_height": 480},
    {"id": "lowest", "label": "Lowest", "max_height": 0},
)
QUALITY_IDS = {item["id"] for item in QUALITY_PRESETS}
FFMPEG_WINGET_ID = "Gyan.FFmpeg"
HLS_ATTR_RE = re.compile(r'([A-Za-z0-9-]+)=("[^"]*"|[^,]*)')
INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


class RecordingError(RuntimeError):
    pass


class FfmpegMissingError(RecordingError):
    pass


class FfmpegInstallError(RecordingError):
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
    output_path: Path
    process: subprocess.Popen
    output_handle: Any
    command: list[str]
    started_at: float
    retry_count: int = 0
    retry_after: float = 0.0
    retry_message: str = ""
    last_returncode: int | None = None


def default_recording_dir() -> Path:
    return Path.home() / "Videos" / "IPTV Multi Player"


def fallback_recording_dir() -> Path:
    return DATA_DIR / "recordings"


def configured_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


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


def public_recording_config(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "ffmpeg": public_ffmpeg(settings),
        "default_dir": str(default_recording_dir()),
        "effective_dir": str(effective_recording_dir(settings, create=False)),
        "quality_presets": list(QUALITY_PRESETS),
    }


def effective_recording_dir(settings: dict[str, Any], create: bool = False) -> Path:
    directory = configured_path(settings.get("recording_dir")) or default_recording_dir()
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            directory = fallback_recording_dir()
            directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_recording_quality(value: Any) -> str:
    quality = str(value or "best").strip().lower()
    return quality if quality in QUALITY_IDS else "best"


def sanitize_filename(value: str, fallback: str = "recording") -> str:
    cleaned = "".join("_" if char in INVALID_FILENAME_CHARS or ord(char) < 32 else char for char in value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:140] or fallback


def recording_path(channel_name: str, settings: dict[str, Any]) -> Path:
    directory = effective_recording_dir(settings, create=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{timestamp} - {sanitize_filename(channel_name)}"
    return directory / f"{base}.ts"


def run_hidden_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def fraction_to_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "0/0":
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denominator = float(right)
            return float(left) / denominator if denominator else None
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_hls_attrs(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, raw in HLS_ATTR_RE.findall(value):
        attrs[key.upper()] = raw.strip().strip('"')
    return attrs


def int_value(value: Any) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def bitrate_label(bits_per_second: int | None) -> str:
    if not bits_per_second:
        return ""
    mbps = bits_per_second / 1_000_000
    if mbps >= 1:
        return f"{mbps:.1f} Mbps"
    return f"{bits_per_second // 1000} Kbps"


def quality_label(height: int | None, width: int | None, bitrate: int | None, fallback: str) -> str:
    parts: list[str] = []
    if height:
        parts.append(f"{height}p")
    elif width:
        parts.append(f"{width}px")
    rate = bitrate_label(bitrate)
    if rate:
        parts.append(rate)
    return " - ".join(parts) if parts else fallback


def fetch_hls_master(url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "IPTV-Multi-Player/1.0",
            "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, text/plain, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        final_url = response.geturl()
        raw = response.read(2_000_000)
    text = raw.decode("utf-8-sig", errors="replace")
    if "#EXTM3U" not in text and "mpegurl" not in content_type:
        return "", final_url
    return text, final_url


def parse_hls_variants(url: str) -> list[QualityOption]:
    if not url.lower().startswith(("http://", "https://")):
        return []

    try:
        text, base_url = fetch_hls_master(url)
    except Exception:
        return []

    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    variants: list[QualityOption] = []
    pending_attrs: dict[str, str] | None = None
    for line in lines:
        if not line:
            continue
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            pending_attrs = parse_hls_attrs(line.split(":", 1)[1] if ":" in line else "")
            continue
        if line.startswith("#"):
            continue
        if pending_attrs is None:
            continue

        width = height = None
        resolution = pending_attrs.get("RESOLUTION", "")
        if "x" in resolution.lower():
            left, right = resolution.lower().split("x", 1)
            width = int_value(left)
            height = int_value(right)
        bitrate = int_value(pending_attrs.get("AVERAGE-BANDWIDTH")) or int_value(pending_attrs.get("BANDWIDTH"))
        fps = fraction_to_float(pending_attrs.get("FRAME-RATE"))
        label = quality_label(height, width, bitrate, f"Variant {len(variants) + 1}")
        variants.append(
            QualityOption(
                id=f"hls:{len(variants)}",
                label=label,
                width=width,
                height=height,
                bitrate=bitrate,
                fps=fps,
                source_url=urljoin(base_url, line),
            )
        )
        pending_attrs = None

    return sorted(variants, key=quality_sort_key, reverse=True)


def run_ffprobe(ffprobe_path: str, url: str) -> dict[str, Any]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_programs",
        "-of",
        "json",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=HTTP_TIMEOUT_SECONDS,
            **run_hidden_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RecordingError("Could not probe stream before the timeout.") from exc
    if result.returncode != 0:
        raise RecordingError((result.stderr or "Could not probe stream.").strip())
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RecordingError(f"Could not parse ffprobe output: {exc}") from exc


def stream_video_quality(stream: dict[str, Any]) -> tuple[int | None, int | None, int | None, float | None]:
    return (
        int_value(stream.get("width")),
        int_value(stream.get("height")),
        int_value(stream.get("bit_rate")),
        fraction_to_float(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
    )


def parse_ffprobe_qualities(payload: dict[str, Any]) -> list[QualityOption]:
    qualities: list[QualityOption] = []
    for program_index, program in enumerate(payload.get("programs") or []):
        streams = program.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if not video:
            continue
        width, height, bitrate, fps = stream_video_quality(video)
        program_id = program.get("program_id") or program.get("program_num")
        if program_id is None:
            continue
        qualities.append(
            QualityOption(
                id=f"program:{program_id}",
                label=quality_label(height, width, bitrate, f"Program {program_index + 1}"),
                width=width,
                height=height,
                bitrate=bitrate,
                fps=fps,
                map_args=["-map", f"p:{program_id}"],
            )
        )

    if len(qualities) > 1:
        return sorted(qualities, key=quality_sort_key, reverse=True)

    video_streams = [
        stream
        for stream in payload.get("streams") or []
        if stream.get("codec_type") == "video"
    ]
    if len(video_streams) > 1:
        qualities = []
        for stream in video_streams:
            stream_index = stream.get("index")
            if stream_index is None:
                continue
            width, height, bitrate, fps = stream_video_quality(stream)
            qualities.append(
                QualityOption(
                    id=f"stream:{stream_index}",
                    label=quality_label(height, width, bitrate, f"Stream {stream_index}"),
                    width=width,
                    height=height,
                    bitrate=bitrate,
                    fps=fps,
                    map_args=["-map", f"0:{stream_index}", "-map", "0:a?"],
                )
            )
        if qualities:
            return sorted(qualities, key=quality_sort_key, reverse=True)

    video = video_streams[0] if video_streams else {}
    width, height, bitrate, fps = stream_video_quality(video)
    return [
        QualityOption(
            id="source",
            label=quality_label(height, width, bitrate, "Source"),
            width=width,
            height=height,
            bitrate=bitrate,
            fps=fps,
        )
    ]


def quality_sort_key(option: QualityOption) -> tuple[int, int]:
    return (option.height or 0, option.bitrate or 0)


def choose_quality(qualities: list[QualityOption], default_quality: str, requested_quality: str | None = None) -> QualityOption:
    if not qualities:
        return QualityOption(id="source", label="Source")

    if requested_quality:
        selected = next((quality for quality in qualities if quality.id == requested_quality), None)
        if selected:
            return selected

    default_quality = sanitize_recording_quality(default_quality)
    sorted_qualities = sorted(qualities, key=quality_sort_key, reverse=True)
    if default_quality == "best":
        return sorted_qualities[0]
    if default_quality == "lowest":
        return sorted_qualities[-1]

    max_height = int(default_quality)
    capped = [
        quality
        for quality in sorted_qualities
        if quality.height is not None and quality.height <= max_height
    ]
    return capped[0] if capped else sorted_qualities[-1]


def recording_map_args(quality: QualityOption) -> list[str]:
    if quality.map_args:
        return quality.map_args
    return ["-map", "0:v?", "-map", "0:a?"]


def retry_delay_seconds(retry_count: int) -> int:
    if retry_count <= 3:
        return 1
    if retry_count <= 6:
        return 3
    if retry_count <= 10:
        return 5
    return 10


def close_output_handle(active: ActiveRecording) -> None:
    try:
        active.output_handle.close()
    except OSError:
        pass


def launch_recording_process(command: list[str], output_path: Path, append: bool) -> tuple[subprocess.Popen, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_handle = output_path.open("ab" if append else "wb")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output_handle,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **run_hidden_kwargs(),
        )
    except Exception:
        output_handle.close()
        raise
    return process, output_handle


def probe_qualities(url: str, settings: dict[str, Any]) -> tuple[list[QualityOption], FfmpegInfo]:
    ffmpeg = find_ffmpeg(settings)
    if not ffmpeg.available:
        return [], ffmpeg

    hls_qualities = parse_hls_variants(url)
    if hls_qualities:
        return hls_qualities, ffmpeg

    try:
        payload = run_ffprobe(ffmpeg.ffprobe_path, url)
        return parse_ffprobe_qualities(payload), ffmpeg
    except RecordingError:
        return [QualityOption(id="source", label="Source")], ffmpeg


def public_status_from_active(active: ActiveRecording) -> dict[str, Any]:
    elapsed = max(0, int(time.time() - active.started_at))
    state = "retrying" if active.retry_after else "recording"
    message = active.retry_message if active.retry_after else "Recording"
    size = 0
    if active.output_path.is_file():
        try:
            size = active.output_path.stat().st_size
        except OSError:
            size = 0
    return {
        "active": True,
        "state": state,
        "message": message,
        "channel_id": active.channel_id,
        "channel_name": active.channel_name,
        "quality_id": active.quality_id,
        "quality_label": active.quality_label,
        "output_path": str(active.output_path),
        "elapsed_seconds": elapsed,
        "size_bytes": size,
        "retry_count": active.retry_count,
    }


def public_terminal_status(active: ActiveRecording, state: str, message: str) -> dict[str, Any]:
    status = public_status_from_active(active)
    status["active"] = False
    status["state"] = state
    status["message"] = message
    return status


def inactive_status() -> dict[str, Any]:
    return {
        "active": False,
        "state": "idle",
        "message": "",
        "channel_id": "",
        "channel_name": "",
        "quality_id": "",
        "quality_label": "",
        "output_path": "",
        "elapsed_seconds": 0,
        "size_bytes": 0,
    }


class RecordingManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: ActiveRecording | None = None
        self._last_output_path: Path | None = None
        self._last_status: dict[str, Any] | None = None

    def _schedule_retry_locked(self, active: ActiveRecording, returncode: int | None) -> None:
        active.retry_count += 1
        active.last_returncode = returncode
        delay = retry_delay_seconds(active.retry_count)
        active.retry_after = time.time() + delay
        code_text = "unknown exit code" if returncode is None else f"exit code {returncode}"
        active.retry_message = f"Stream cut out ({code_text}). Retrying automatically..."
        self._last_output_path = active.output_path

    def _restart_active_locked(self, active: ActiveRecording) -> None:
        try:
            process, output_handle = launch_recording_process(active.command, active.output_path, append=True)
        except Exception as exc:  # noqa: BLE001 - keep retrying transient launch failures
            active.retry_count += 1
            delay = retry_delay_seconds(active.retry_count)
            active.retry_after = time.time() + delay
            active.retry_message = f"Could not restart recording: {exc}. Retrying automatically..."
            return

        active.process = process
        active.output_handle = output_handle
        active.retry_after = 0.0
        active.retry_message = ""

    def _clear_finished_locked(self) -> None:
        if self._active and self._active.process.poll() is not None:
            active = self._active
            returncode = active.process.returncode
            if not active.retry_after:
                close_output_handle(active)
                self._schedule_retry_locked(active, returncode)
                return
            if time.time() >= active.retry_after:
                self._restart_active_locked(active)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._clear_finished_locked()
            if not self._active:
                return self._last_status or inactive_status()
            return public_status_from_active(self._active)

    def prepare(self, channel: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._clear_finished_locked()
            if self._active:
                return {
                    "ffmpeg": public_ffmpeg(settings),
                    "recording": self.status(),
                    "qualities": [],
                    "selected_quality_id": "",
                    "can_start": False,
                    "message": "Another stream is already recording.",
                }

        qualities, ffmpeg = probe_qualities(str(channel.get("url") or ""), settings)
        selected = choose_quality(qualities, settings.get("recording_default_quality", "best"))
        return {
            "ffmpeg": {
                "available": ffmpeg.available,
                "path": ffmpeg.ffmpeg_path,
                "ffprobe_path": ffmpeg.ffprobe_path,
                "message": ffmpeg.message,
                "install_id": FFMPEG_WINGET_ID,
            },
            "recording": self.status(),
            "qualities": [quality.public() for quality in qualities],
            "selected_quality_id": selected.id,
            "can_start": ffmpeg.available,
            "message": ffmpeg.message,
        }

    def start(self, channel: dict[str, Any], settings: dict[str, Any], quality_id: str | None) -> dict[str, Any]:
        with self._lock:
            self._clear_finished_locked()
            if self._active:
                raise RecordingError("Another stream is already recording.")

        qualities, ffmpeg = probe_qualities(str(channel.get("url") or ""), settings)
        if not ffmpeg.available:
            raise FfmpegMissingError(ffmpeg.message)

        quality = choose_quality(qualities, settings.get("recording_default_quality", "best"), quality_id)
        input_url = quality.source_url or str(channel.get("url") or "")
        if not input_url:
            raise RecordingError("Channel has no stream URL to record.")

        output_path = recording_path(str(channel.get("name") or "Recording"), settings)
        command = [
            ffmpeg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            input_url,
            *recording_map_args(quality),
            "-c",
            "copy",
            "-flush_packets",
            "1",
            "-f",
            "mpegts",
            "pipe:1",
        ]

        process, output_handle = launch_recording_process(command, output_path, append=False)

        active = ActiveRecording(
            channel_id=str(channel.get("id") or ""),
            channel_name=str(channel.get("name") or "Recording"),
            quality_id=quality.id,
            quality_label=quality.label,
            output_path=output_path,
            process=process,
            output_handle=output_handle,
            command=command,
            started_at=time.time(),
        )
        with self._lock:
            self._active = active
            self._last_output_path = output_path
            self._last_status = None
            return public_status_from_active(active)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            if not active:
                return inactive_status()

        process = active.process
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write("q\n")
                    process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=4)

        close_output_handle(active)
        with self._lock:
            self._last_output_path = active.output_path
            self._active = None
            active.retry_after = 0.0
            active.retry_message = ""
            self._last_status = public_terminal_status(active, "stopped", "Recording stopped.")
            return self._last_status

    def active_or_last_path(self) -> Path | None:
        with self._lock:
            self._clear_finished_locked()
            if self._active:
                return self._active.output_path
            return self._last_output_path

    def active_or_last_reveal_path(self) -> Path | None:
        with self._lock:
            self._clear_finished_locked()
            if self._active:
                return self._active.output_path
            return self._last_output_path

    def open_recording(self, settings: dict[str, Any], player_id: Any = None) -> dict[str, Any]:
        path = self.active_or_last_path()
        if not path or not path.exists():
            raise RecordingError("Recording file was not found.")
        player, executable = launch_player([str(path)], settings, player_id)
        return {"path": str(path), "player": player, "label": player_label(player)}

    def reveal_recording(self) -> dict[str, Any]:
        path = self.active_or_last_reveal_path()
        if not path or not path.exists():
            raise RecordingError("Recording file was not found.")

        folder = path.parent
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", str(folder)], **run_hidden_kwargs())
        elif sys_platform := shutil.which("xdg-open"):
            subprocess.Popen([sys_platform, str(folder)])
        else:
            raise RecordingError("Could not open the recording folder on this platform.")
        return {"path": str(path), "folder": str(folder)}


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


recording_manager = RecordingManager()
