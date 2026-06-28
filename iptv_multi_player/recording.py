from __future__ import annotations

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

from .config import HTTP_TIMEOUT_SECONDS
from .players import launch_player, player_label
from .recording_clips import (
    CLIP_DURATION_PRESETS,
    CLIP_SEGMENT_SECONDS,
    cleanup_clip_buffer,
    clip_buffer_dir,
    clip_path,
    clip_segment_count,
    clip_segment_files,
    remux_clip_segments,
    reset_clip_buffer_root,
    sanitize_clip_seconds,
)
from .recording_ffmpeg import (
    FFMPEG_WINGET_ID,
    command_text,
    find_ffmpeg,
    install_ffmpeg,
    public_ffmpeg,
    run_hidden_kwargs,
)
from .recording_log import append_recording_log, recording_log_path, reset_recording_session_log
from .recording_paths import default_recording_dir, effective_recording_dir, recording_path
from .recording_types import (
    ActiveRecording,
    FfmpegInfo,
    FfmpegMissingError,
    FfmpegInstallError,
    QualityOption,
    QualityUnavailableError,
    RecordingError,
)


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
HLS_ATTR_RE = re.compile(r'([A-Za-z0-9-]+)=("[^"]*"|[^,]*)')


def public_recording_config(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "ffmpeg": public_ffmpeg(settings),
        "default_dir": str(default_recording_dir()),
        "effective_dir": str(effective_recording_dir(settings, create=False)),
        "log_path": str(recording_log_path()),
        "quality_presets": list(QUALITY_PRESETS),
        "clip_duration_presets": list(CLIP_DURATION_PRESETS),
        "default_clip_seconds": sanitize_clip_seconds(settings.get("recording_clip_seconds")),
    }

def sanitize_recording_quality(value: Any) -> str:
    quality = str(value or "best").strip().lower()
    return quality if quality in QUALITY_IDS else "best"


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
    append_recording_log("hls master fetch", [url])
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
        status = getattr(response, "status", "")
    text = raw.decode("utf-8-sig", errors="replace")
    append_recording_log(
        "hls master response",
        [
            f"status={status}",
            f"content_type={content_type}",
            f"final_url={final_url}",
            f"bytes={len(raw)}",
            f"has_extm3u={'#EXTM3U' in text}",
        ],
    )
    if "#EXTM3U" not in text and "mpegurl" not in content_type:
        append_recording_log("hls master rejected", [text[:1000]])
        return "", final_url
    return text, final_url


def parse_hls_variants(url: str) -> list[QualityOption]:
    if not url.lower().startswith(("http://", "https://")):
        return []

    try:
        text, base_url = fetch_hls_master(url)
    except Exception as exc:  # noqa: BLE001 - probing should fall back to ffprobe
        append_recording_log("hls variant parse failed", [f"{type(exc).__name__}: {exc}"])
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
    append_recording_log("ffprobe start", [command_text(command)])
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
        append_recording_log("ffprobe timeout", [command_text(command)])
        raise RecordingError("Could not probe stream before the timeout.") from exc
    append_recording_log(
        "ffprobe finished",
        [
            f"returncode={result.returncode}",
            "stdout:",
            result.stdout or "",
            "stderr:",
            result.stderr or "",
        ],
    )
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


def quality_preset(value: Any) -> dict[str, Any]:
    preset_id = sanitize_recording_quality(value)
    return next((preset for preset in QUALITY_PRESETS if preset["id"] == preset_id), QUALITY_PRESETS[0])


def quality_preset_label(value: Any) -> str:
    return str(quality_preset(value)["label"])


def choose_quality_for_preset(qualities: list[QualityOption], preset_id: str) -> QualityOption | None:
    if not qualities:
        return QualityOption(id="source", label="Source")

    default_quality = sanitize_recording_quality(preset_id)
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
    return capped[0] if capped else None


def choose_quality(qualities: list[QualityOption], default_quality: str, requested_quality: str | None = None) -> QualityOption:
    if not qualities:
        return QualityOption(id="source", label="Source")

    if requested_quality:
        selected = next((quality for quality in qualities if quality.id == requested_quality), None)
        if selected:
            return selected

    selected = choose_quality_for_preset(qualities, default_quality)
    if selected:
        return selected

    raise QualityUnavailableError(f"{quality_preset_label(default_quality)} is not available for this stream.")


def quality_unavailable_payload(qualities: list[QualityOption], requested_quality: str) -> dict[str, Any]:
    label = quality_preset_label(requested_quality)
    return {
        "quality_unavailable": True,
        "requested_quality_id": sanitize_recording_quality(requested_quality),
        "requested_quality_label": label,
        "message": f"{label} is not available for this stream. Choose one of the available qualities.",
        "qualities": [quality.public() for quality in qualities],
    }


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


def close_recording_handles(active: ActiveRecording) -> None:
    if active.output_handle:
        try:
            active.output_handle.close()
        except OSError:
            pass
    try:
        active.log_handle.close()
    except OSError:
        pass


def launch_recording_process(command: list[str], output_path: Path, append: bool) -> tuple[subprocess.Popen, Any, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_handle = output_path.open("ab" if append else "wb")
    log_handle = recording_log_path().open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] ffmpeg recording {'restart' if append else 'start'}\n")
    log_handle.write(f"{command_text(command)}\n\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output_handle,
            stderr=log_handle,
            close_fds=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **run_hidden_kwargs(),
        )
    except Exception:
        output_handle.close()
        log_handle.close()
        raise
    return process, output_handle, log_handle


def launch_clip_process(command: list[str]) -> tuple[subprocess.Popen, Any]:
    log_handle = recording_log_path().open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] ffmpeg clip buffer start\n")
    log_handle.write(f"{command_text(command)}\n\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=log_handle,
            close_fds=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **run_hidden_kwargs(),
        )
    except Exception:
        log_handle.close()
        raise
    return process, log_handle


def stop_recording_process(active: ActiveRecording) -> None:
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
    is_clip = active.mode == "clip"
    message = active.retry_message if active.retry_after else ("Clip buffer active" if is_clip else "Recording")
    size = 0
    output_path = active.last_clip_path if is_clip else active.output_path
    if output_path and output_path.is_file():
        try:
            size = output_path.stat().st_size
        except OSError:
            size = 0
    elif is_clip:
        for segment in clip_segment_files(active.buffer_dir):
            try:
                size += segment.stat().st_size
            except OSError:
                pass
    return {
        "id": active.channel_id,
        "active": True,
        "state": state,
        "message": message,
        "mode": active.mode,
        "channel_id": active.channel_id,
        "channel_name": active.channel_name,
        "quality_id": active.quality_id,
        "quality_label": active.quality_label,
        "output_path": str(output_path) if output_path else "",
        "elapsed_seconds": elapsed,
        "size_bytes": size,
        "clip_seconds": active.clip_seconds,
        "clip_ready_seconds": min(active.clip_seconds, elapsed) if is_clip else 0,
        "clip_path": str(active.last_clip_path) if active.last_clip_path else "",
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
        "id": "",
        "active": False,
        "state": "idle",
        "message": "",
        "mode": "recording",
        "channel_id": "",
        "channel_name": "",
        "quality_id": "",
        "quality_label": "",
        "output_path": "",
        "elapsed_seconds": 0,
        "size_bytes": 0,
        "clip_seconds": 0,
        "clip_ready_seconds": 0,
        "clip_path": "",
        "items": [],
        "active_count": 0,
    }


class RecordingManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, ActiveRecording] = {}
        self._last_output_path: Path | None = None
        self._last_status: dict[str, Any] | None = None
        reset_clip_buffer_root()

    def _active_statuses_locked(self) -> list[dict[str, Any]]:
        return [
            public_status_from_active(active)
            for active in sorted(self._active.values(), key=lambda item: item.started_at)
        ]

    def _public_status_locked(self, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        items = self._active_statuses_locked()
        if items:
            status = dict(items[-1])
            status["items"] = items
            status["active_count"] = len(items)
            return status

        status = dict(fallback or self._last_status or inactive_status())
        status["items"] = []
        status["active_count"] = 0
        return status

    def _active_for_channel_locked(self, channel_id: str | None = None, mode: str | None = None) -> ActiveRecording | None:
        active = self._active.get(channel_id or "") if channel_id else next(iter(self._active.values()), None)
        if active and mode and active.mode != mode:
            return None
        return active

    def _schedule_retry_locked(self, active: ActiveRecording, returncode: int | None) -> None:
        active.retry_count += 1
        active.last_returncode = returncode
        delay = retry_delay_seconds(active.retry_count)
        active.retry_after = time.time() + delay
        code_text = "unknown exit code" if returncode is None else f"exit code {returncode}"
        active.retry_message = f"Stream cut out ({code_text}). Retrying automatically..."
        if active.output_path:
            self._last_output_path = active.output_path
        append_recording_log(
            "ffmpeg exited",
            [
                f"channel={active.channel_name}",
                f"mode={active.mode}",
                f"returncode={returncode}",
                f"retry_count={active.retry_count}",
                f"retry_delay_seconds={delay}",
                f"output_path={active.output_path or ''}",
                f"buffer_dir={active.buffer_dir or ''}",
            ],
        )

    def _restart_active_locked(self, active: ActiveRecording) -> None:
        try:
            if active.mode == "clip":
                process, log_handle = launch_clip_process(active.command)
                output_handle = None
            elif active.output_path:
                process, output_handle, log_handle = launch_recording_process(active.command, active.output_path, append=True)
            else:
                raise RecordingError("Recording output path was lost.")
        except Exception as exc:  # noqa: BLE001 - keep retrying transient launch failures
            active.retry_count += 1
            delay = retry_delay_seconds(active.retry_count)
            active.retry_after = time.time() + delay
            active.retry_message = f"Could not restart recording: {exc}. Retrying automatically..."
            append_recording_log(
                "ffmpeg restart failed",
                [
                    f"channel={active.channel_name}",
                    f"{type(exc).__name__}: {exc}",
                    f"retry_count={active.retry_count}",
                    f"retry_delay_seconds={delay}",
                ],
            )
            return

        active.process = process
        active.output_handle = output_handle
        active.log_handle = log_handle
        active.retry_after = 0.0
        active.retry_message = ""

    def _clear_finished_locked(self) -> None:
        for active in list(self._active.values()):
            if active.process.poll() is None:
                continue
            returncode = active.process.returncode
            if not active.retry_after:
                close_recording_handles(active)
                self._schedule_retry_locked(active, returncode)
                continue
            if time.time() >= active.retry_after:
                self._restart_active_locked(active)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._clear_finished_locked()
            return self._public_status_locked()

    def prepare(self, channel: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(channel.get("id") or "")
        with self._lock:
            self._clear_finished_locked()
            if channel_id in self._active:
                return {
                    "ffmpeg": public_ffmpeg(settings),
                    "recording": self._public_status_locked(),
                    "qualities": [],
                    "selected_quality_id": "",
                    "can_start": False,
                    "message": "This channel is already recording or buffering a clip.",
                }

        qualities, ffmpeg = probe_qualities(str(channel.get("url") or ""), settings)
        requested_quality = sanitize_recording_quality(settings.get("recording_default_quality", "best"))
        selected = choose_quality_for_preset(qualities, requested_quality)
        quality_unavailable = selected is None
        if quality_unavailable:
            append_recording_log(
                "recording quality unavailable",
                [
                    f"channel={channel.get('name') or 'Recording'}",
                    f"requested={quality_preset_label(requested_quality)}",
                    "available:",
                    *[quality.label for quality in qualities],
                ],
            )
            selected = sorted(qualities, key=quality_sort_key, reverse=True)[0] if qualities else QualityOption(id="source", label="Source")
            unavailable = quality_unavailable_payload(qualities, requested_quality)
        else:
            unavailable = {
                "quality_unavailable": False,
                "requested_quality_id": requested_quality,
                "requested_quality_label": quality_preset_label(requested_quality),
            }
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
            **unavailable,
        }

    def start(
        self,
        channel: dict[str, Any],
        settings: dict[str, Any],
        quality_id: str | None,
        clip_seconds: int = 0,
    ) -> dict[str, Any]:
        channel_id = str(channel.get("id") or "")
        with self._lock:
            self._clear_finished_locked()
            if channel_id in self._active:
                raise RecordingError("This channel is already recording or buffering a clip.")

        qualities, ffmpeg = probe_qualities(str(channel.get("url") or ""), settings)
        if not ffmpeg.available:
            raise FfmpegMissingError(ffmpeg.message)

        quality = choose_quality(qualities, settings.get("recording_default_quality", "best"), quality_id)
        input_url = quality.source_url or str(channel.get("url") or "")
        if not input_url:
            raise RecordingError("Channel has no stream URL to record.")

        channel_name = str(channel.get("name") or "Recording")
        mode = "clip" if clip_seconds else "recording"
        output_path: Path | None = None
        output_handle: Any | None = None
        buffer_dir: Path | None = None

        if mode == "clip":
            clip_seconds = sanitize_clip_seconds(clip_seconds)
            buffer_dir = clip_buffer_dir(channel_name)
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
                "-f",
                "segment",
                "-segment_time",
                str(CLIP_SEGMENT_SECONDS),
                "-segment_wrap",
                str(clip_segment_count(clip_seconds)),
                "-reset_timestamps",
                "1",
                "-segment_format",
                "mpegts",
                str(buffer_dir / "buffer_%05d.ts"),
            ]
            process, log_handle = launch_clip_process(command)
        else:
            output_path = recording_path(channel_name, settings)
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
            process, output_handle, log_handle = launch_recording_process(command, output_path, append=False)

        active = ActiveRecording(
            channel_id=channel_id,
            channel_name=channel_name,
            quality_id=quality.id,
            quality_label=quality.label,
            output_path=output_path,
            process=process,
            output_handle=output_handle,
            log_handle=log_handle,
            command=command,
            started_at=time.time(),
            mode=mode,
            clip_seconds=clip_seconds if mode == "clip" else 0,
            buffer_dir=buffer_dir,
        )
        with self._lock:
            self._clear_finished_locked()
            if active.channel_id in self._active:
                stop_recording_process(active)
                close_recording_handles(active)
                if active.mode == "clip":
                    cleanup_clip_buffer(active)
                raise RecordingError("This channel is already recording or buffering a clip.")
            self._active[active.channel_id] = active
            if output_path:
                self._last_output_path = output_path
            self._last_status = None
            append_recording_log(
                "recording active" if mode == "recording" else "clip buffer active",
                [
                    f"channel={active.channel_name}",
                    f"quality={active.quality_label}",
                    f"output_path={active.output_path or ''}",
                    f"clip_seconds={active.clip_seconds}",
                    f"buffer_dir={active.buffer_dir or ''}",
                ],
            )
            return self._public_status_locked()

    def stop(self, channel_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            active = self._active_for_channel_locked(channel_id)
            if not active:
                return self._public_status_locked()

        process = active.process
        stop_recording_process(active)
        close_recording_handles(active)
        with self._lock:
            if active.last_clip_path:
                self._last_output_path = active.last_clip_path
            elif active.output_path:
                self._last_output_path = active.output_path
            if self._active.get(active.channel_id) is active:
                del self._active[active.channel_id]
            active.retry_after = 0.0
            active.retry_message = ""
            message = "Clip buffer stopped." if active.mode == "clip" else "Recording stopped."
            terminal_status = public_terminal_status(active, "stopped", message)
            self._last_status = terminal_status
            append_recording_log(
                "recording stopped" if active.mode == "recording" else "clip buffer stopped",
                [
                    f"channel={active.channel_name}",
                    f"mode={active.mode}",
                    f"returncode={process.returncode}",
                    f"output_path={active.output_path or ''}",
                    f"last_clip_path={active.last_clip_path or ''}",
                ],
            )
            if active.mode == "clip":
                cleanup_clip_buffer(active)
            return self._public_status_locked(terminal_status)

    def save_clip(self, settings: dict[str, Any], channel_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._clear_finished_locked()
            active = self._active_for_channel_locked(channel_id, mode="clip")
            if not active:
                raise RecordingError("No clip buffer is active.")

            segments = clip_segment_files(active.buffer_dir)
            if len(segments) > 1:
                segments = segments[:-1]
            segment_limit = max(1, (active.clip_seconds + CLIP_SEGMENT_SECONDS - 1) // CLIP_SEGMENT_SECONDS + 1)
            segments = segments[-segment_limit:]
            if not segments:
                raise RecordingError("The clip buffer is not ready yet.")

            channel_name = active.channel_name
            clip_seconds = active.clip_seconds

        ffmpeg = find_ffmpeg(settings)
        if not ffmpeg.available:
            raise FfmpegMissingError(ffmpeg.message)

        output_path = clip_path(channel_name, settings)
        remux_clip_segments(ffmpeg.ffmpeg_path, segments, output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RecordingError("Could not save clip because the buffer was empty.")

        with self._lock:
            active.last_clip_path = output_path
            self._last_output_path = output_path
            append_recording_log(
                "clip saved",
                [
                    f"channel={channel_name}",
                    f"clip_seconds={clip_seconds}",
                    f"segments={len(segments)}",
                    f"output_path={output_path}",
                ],
            )
            return self._public_status_locked()

    def active_or_last_path(self, channel_id: str | None = None) -> Path | None:
        with self._lock:
            self._clear_finished_locked()
            active = self._active_for_channel_locked(channel_id)
            if active:
                return active.last_clip_path if active.mode == "clip" else active.output_path
            return self._last_output_path

    def active_or_last_reveal_path(self, channel_id: str | None = None) -> Path | None:
        with self._lock:
            self._clear_finished_locked()
            active = self._active_for_channel_locked(channel_id)
            if active:
                return active.last_clip_path if active.mode == "clip" else active.output_path
            return self._last_output_path

    def open_recording(self, settings: dict[str, Any], player_id: Any = None, channel_id: str | None = None) -> dict[str, Any]:
        path = self.active_or_last_path(channel_id)
        if not path or not path.exists():
            raise RecordingError("Recording file was not found.")
        player, executable = launch_player([str(path)], settings, player_id)
        return {"path": str(path), "player": player, "label": player_label(player, settings)}

    def reveal_recording(self, channel_id: str | None = None) -> dict[str, Any]:
        path = self.active_or_last_reveal_path(channel_id)
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

recording_manager = RecordingManager()
