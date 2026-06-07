from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
import time
from typing import Any

from .config import (
    API_SPORTS_KEY_NAME,
    DATA_DIR,
    DEFAULT_PLAYER,
    ENV_PATH,
    ESPN_CONFIG,
    LIBRARY_PATH,
    SOURCE_DIR,
    SPORTS_CACHE_PATH,
    SPORTS_CONFIG,
)

def default_state() -> dict[str, Any]:
    return {
        "sources": [],
        "channels": {},
        "favorites": [],
        "queue": [],
        "selected_source_id": "all",
        "settings": {
            "grid_size": "3x3",
            "selected_player": DEFAULT_PLAYER,
            "gridplayer_path": "",
            "mpv_path": "",
            "vlc_path": "",
            "auto_open_queue": False,
            "ui_zoom": 100,
            "ui_sidebar_width": 250,
            "pinned_categories": [],
        },
    }


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)


def read_state() -> dict[str, Any]:
    ensure_data_dirs()
    if not LIBRARY_PATH.exists():
        return default_state()

    try:
        loaded = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_state()

    state = default_state()
    state.update(loaded)
    state["settings"] = {**default_state()["settings"], **loaded.get("settings", {})}
    state.setdefault("sources", [])
    state.setdefault("channels", {})
    state.setdefault("favorites", [])
    state.setdefault("queue", [])
    return state


def write_state(state: dict[str, Any]) -> None:
    ensure_data_dirs()
    temp_path = LIBRARY_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(LIBRARY_PATH)


def default_sports_cache() -> dict[str, Any]:
    return {
        "version": 1,
        "espn": {
            sport: {
                "endpoints": {},
                "last_error": "",
            }
            for sport in ESPN_CONFIG
        },
        "sports": {
            sport: {
                "calls": {},
                "dates": {},
                "last_error": "",
            }
            for sport in SPORTS_CONFIG
        },
    }


def read_sports_cache() -> dict[str, Any]:
    ensure_data_dirs()
    if not SPORTS_CACHE_PATH.exists():
        return default_sports_cache()

    try:
        loaded = json.loads(SPORTS_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_sports_cache()

    cache = default_sports_cache()
    cache.update(loaded)
    cache.setdefault("espn", {})
    for sport in ESPN_CONFIG:
        existing = cache["espn"].setdefault(sport, {})
        existing.setdefault("endpoints", {})
        existing.setdefault("last_error", "")
    cache.setdefault("sports", {})
    for sport in SPORTS_CONFIG:
        existing = cache["sports"].setdefault(sport, {})
        existing.setdefault("calls", {})
        existing.setdefault("dates", {})
        existing.setdefault("last_error", "")
    return cache


def write_sports_cache(cache: dict[str, Any]) -> None:
    ensure_data_dirs()
    temp_path = SPORTS_CACHE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(SPORTS_CACHE_PATH)


def read_env_file() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}

    values: dict[str, str] = {}
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def quote_env_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env_value(key: str, value: str) -> None:
    value = value.strip()
    lines: list[str] = []
    if ENV_PATH.exists():
        try:
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    found = False
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            next_lines.append(line)
            continue

        current_key = stripped.split("=", 1)[0].strip()
        if current_key != key:
            next_lines.append(line)
            continue

        found = True
        if value:
            next_lines.append(f"{key}={quote_env_value(value)}")

    if not found and value:
        next_lines.append(f"{key}={quote_env_value(value)}")

    if next_lines or ENV_PATH.exists():
        ENV_PATH.write_text(("\n".join(next_lines).rstrip() + "\n") if next_lines else "", encoding="utf-8")


def api_sports_key() -> str:
    return os.environ.get(API_SPORTS_KEY_NAME, "").strip() or read_env_file().get(API_SPORTS_KEY_NAME, "").strip()


def make_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def now_ts() -> int:
    return int(time.time())


def local_date() -> str:
    return datetime.now().astimezone().date().isoformat()


def sports_query_dates() -> list[str]:
    today = datetime.now().astimezone().date()
    return [today.isoformat(), (today + timedelta(days=1)).isoformat()]


def local_time_text(timestamp: Any) -> str:
    try:
        local_dt = datetime.fromtimestamp(int(timestamp)).astimezone()
    except (OSError, OverflowError, TypeError, ValueError):
        return ""

    time_part = local_dt.strftime("%I:%M %p").lstrip("0")
    if local_dt.date() == datetime.now().astimezone().date():
        return time_part
    return f"{local_dt.strftime('%b')} {local_dt.day}, {time_part}"
