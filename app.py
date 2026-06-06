from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path
from typing import Any
import unicodedata

from flask import Flask, jsonify, render_template, request, send_file

try:
    import webview
except ImportError:  # pragma: no cover - exercised only on machines without pywebview
    webview = None

try:
    from waitress import serve as waitress_serve
except ImportError:  # pragma: no cover
    waitress_serve = None


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_asset_dir() -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir)
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
ASSET_DIR = get_asset_dir()
DATA_DIR = APP_DIR / "data"
SOURCE_DIR = DATA_DIR / "sources"
LIBRARY_PATH = DATA_DIR / "library.json"
QUEUE_EXPORT_PATH = DATA_DIR / "gridplayer-queue.m3u"
SPORTS_CACHE_PATH = DATA_DIR / "sports_cache.json"
ENV_PATH = APP_DIR / ".env"

MAX_QUEUE_ITEMS = 16
HTTP_TIMEOUT_SECONDS = 18
SPORTS_REFRESH_SECONDS = 30 * 60
SPORTS_DAILY_CALL_LIMIT = 100
DESKTOP_MODE = False

ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
MATCHUP_RE = re.compile(r"^(.+?)\s+(?:vs\.?|v|@)\s+(.+)$", re.IGNORECASE)

SPORTS_CONFIG = {
    "football": {
        "group": "Football",
        "base_url": "https://v3.football.api-sports.io/fixtures",
        "live_codes": {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"},
        "scheduled_codes": {"TBD", "NS"},
        "finished_codes": {"FT", "AET", "PEN"},
        "inactive_codes": {"PST", "CANC", "ABD", "AWD", "WO"},
    },
    "basketball": {
        "group": "Basketball",
        "base_url": "https://v1.basketball.api-sports.io/games",
        "live_codes": {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"},
        "scheduled_codes": {"NS"},
        "finished_codes": {"FT", "AOT"},
        "inactive_codes": {"POST", "CANC", "SUSP", "AWD", "ABD"},
    },
    "mma": {
        "group": "Combat Sports",
        "base_url": "https://v1.mma.api-sports.io/fights",
        "live_codes": {"IN", "PF", "LIVE", "EOR", "WO"},
        "scheduled_codes": {"NS"},
        "finished_codes": {"FT"},
        "inactive_codes": {"CANC", "PST"},
    },
}

SPORT_BY_GROUP = {config["group"]: sport for sport, config in SPORTS_CONFIG.items()}
TEAM_ALIASES = {
    "bosnia herzegovina": "bosnia and herzegovina",
    "czechia": "czech republic",
    "south korea": "korea republic",
    "turkiye": "turkey",
    "united states": "usa",
}

app = Flask(
    __name__,
    template_folder=str(ASSET_DIR / "templates"),
    static_folder=str(ASSET_DIR / "static"),
)
state_lock = threading.RLock()


def default_state() -> dict[str, Any]:
    return {
        "sources": [],
        "channels": {},
        "favorites": [],
        "queue": [],
        "selected_source_id": "all",
        "settings": {
            "grid_size": "3x3",
            "gridplayer_path": "",
            "auto_open_queue": False,
            "ui_zoom": 100,
            "ui_sidebar_width": 250,
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


def api_sports_key() -> str:
    return os.environ.get("API_SPORTS_KEY", "").strip() or read_env_file().get("API_SPORTS_KEY", "").strip()


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


def normalize_team_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(fc|sc|cf|club|the)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return TEAM_ALIASES.get(text, text)


def team_match_score(left: str, right: str) -> float:
    left_norm = normalize_team_name(left)
    right_norm = normalize_team_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.92
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def parse_channel_matchup(channel: dict[str, Any]) -> dict[str, str] | None:
    sport = SPORT_BY_GROUP.get(channel.get("group", ""))
    if not sport:
        return None

    name = str(channel.get("name", "")).strip()
    title = re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()
    if sport == "mma":
        match = MATCHUP_RE.search(title)
        return {
            "sport": sport,
            "home": match.group(1).split(":", 1)[-1].strip() if match else "",
            "away": match.group(2).strip() if match else "",
            "title": title,
        }

    match = MATCHUP_RE.match(title)
    if match is None:
        return None

    return {
        "sport": sport,
        "home": match.group(1).strip(),
        "away": match.group(2).strip(),
        "title": title,
    }


def sports_calls_today(sport_cache: dict[str, Any]) -> int:
    return int(sport_cache.setdefault("calls", {}).get(local_date(), 0) or 0)


def increment_sports_calls(sport_cache: dict[str, Any]) -> None:
    calls = sport_cache.setdefault("calls", {})
    today = local_date()
    calls[today] = int(calls.get(today, 0) or 0) + 1
    for day in list(calls):
        if day != today:
            del calls[day]


def fetch_sports_events(sport: str, date_value: str, key: str) -> list[dict[str, Any]]:
    config = SPORTS_CONFIG[sport]
    url = f"{config['base_url']}?{urlencode({'date': date_value})}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GridPlayer-IPTV/1.0",
            "Accept": "application/json",
            "x-apisports-key": key,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"API-SPORTS returned errors: {errors}")

    events = payload.get("response", [])
    if not isinstance(events, list):
        raise RuntimeError("API-SPORTS returned an unexpected response shape.")
    for event in events:
        if isinstance(event, dict):
            event["_sport"] = sport
    return events


def ensure_sports_cache_for(channels: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_sports = {
        sport
        for channel in channels
        for sport in [SPORT_BY_GROUP.get(channel.get("group", ""))]
        if sport
    }
    cache = read_sports_cache()
    key = api_sports_key()
    now = now_ts()
    query_dates = sports_query_dates()
    query_date_set = set(query_dates)
    changed = False
    meta = {
        "configured": bool(key),
        "refresh_seconds": SPORTS_REFRESH_SECONDS,
        "daily_call_limit": SPORTS_DAILY_CALL_LIMIT,
        "sports": {},
    }

    for sport in requested_sports:
        sport_cache = cache["sports"].setdefault(sport, {"calls": {}, "dates": {}, "last_error": ""})
        calls_used = sports_calls_today(sport_cache)

        for date_value in query_dates:
            date_cache = sport_cache.setdefault("dates", {}).setdefault(date_value, {"last_fetch": 0, "events": []})
            stale = now - int(date_cache.get("last_fetch", 0) or 0) >= SPORTS_REFRESH_SECONDS

            if key and stale and calls_used < SPORTS_DAILY_CALL_LIMIT:
                try:
                    increment_sports_calls(sport_cache)
                    calls_used += 1
                    date_cache["events"] = fetch_sports_events(sport, date_value, key)
                    date_cache["last_fetch"] = now
                    sport_cache["last_error"] = ""
                except Exception as exc:  # noqa: BLE001 - surfaced as cache status, not a fatal app error
                    sport_cache["last_error"] = str(exc)
                changed = True
            elif key and stale and calls_used >= SPORTS_DAILY_CALL_LIMIT:
                sport_cache["last_error"] = "Daily API call limit reached for this sport."

        for date_key in list(sport_cache.get("dates", {})):
            if date_key not in query_date_set:
                del sport_cache["dates"][date_key]
                changed = True

        date_entries = [sport_cache.get("dates", {}).get(date_value, {}) for date_value in query_dates]
        last_fetches = [int(item.get("last_fetch", 0) or 0) for item in date_entries]
        event_count = sum(len(item.get("events", [])) for item in date_entries)
        stale = any(now - last_fetch >= SPORTS_REFRESH_SECONDS for last_fetch in last_fetches)

        meta["sports"][sport] = {
            "calls_used": calls_used,
            "dates": query_dates,
            "last_fetch": min(last_fetches) if last_fetches else 0,
            "event_count": event_count,
            "error": sport_cache.get("last_error", ""),
            "stale": stale,
        }

    if changed:
        write_sports_cache(cache)

    return cache, meta


def event_team_names(event: dict[str, Any]) -> tuple[str, str]:
    if event.get("_sport") == "mma" or "fighters" in event:
        fighters = event.get("fighters") or {}
        first = fighters.get("first") or {}
        second = fighters.get("second") or {}
        return str(first.get("name") or ""), str(second.get("name") or "")

    teams = event.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    return str(home.get("name") or ""), str(away.get("name") or "")


def event_status(event: dict[str, Any], sport: str) -> dict[str, Any]:
    if sport == "football":
        status = ((event.get("fixture") or {}).get("status") or {})
    elif sport == "mma":
        status = event.get("status") or {}
    else:
        status = event.get("status") or {}
    return status if isinstance(status, dict) else {}


def event_timestamp(event: dict[str, Any], sport: str) -> Any:
    if sport == "football":
        return (event.get("fixture") or {}).get("timestamp")
    if sport == "mma":
        return event.get("timestamp")
    return event.get("timestamp")


def event_id(event: dict[str, Any], sport: str) -> Any:
    if sport == "football":
        return (event.get("fixture") or {}).get("id")
    if sport == "mma":
        return event.get("id")
    return event.get("id")


def event_score(event: dict[str, Any], sport: str) -> str:
    if sport == "mma":
        return ""

    if sport == "football":
        goals = event.get("goals") or {}
        home = goals.get("home")
        away = goals.get("away")
    else:
        scores = event.get("scores") or {}
        home_score = scores.get("home") or {}
        away_score = scores.get("away") or {}
        home = home_score.get("total") if isinstance(home_score, dict) else None
        away = away_score.get("total") if isinstance(away_score, dict) else None

    if home is None or away is None:
        return ""
    return f"{home}-{away}"


def game_metadata(event: dict[str, Any], sport: str, confidence: float) -> dict[str, Any]:
    config = SPORTS_CONFIG[sport]
    status = event_status(event, sport)
    code = str(status.get("short") or "").upper()
    status_long = str(status.get("long") or code or "Unknown")
    elapsed = status.get("elapsed")
    timer = status.get("timer")
    score = event_score(event, sport)
    start_time = local_time_text(event_timestamp(event, sport))
    home, away = event_team_names(event)

    if code in config["live_codes"]:
        if sport == "mma":
            text = "Live" if code == "LIVE" else status_long
        else:
            detail = str(timer or elapsed or code)
            suffix = f" {detail}'" if sport == "football" and str(detail).isdigit() else f" {detail}" if detail else ""
            text = f"Live{suffix}"
        kind = "live"
    elif code in config["scheduled_codes"]:
        text = f"Starts {start_time}" if start_time else "Scheduled"
        kind = "scheduled"
    elif code in config["finished_codes"]:
        text = "Final"
        kind = "final"
    elif code in config["inactive_codes"]:
        text = status_long
        kind = "inactive"
    else:
        text = status_long or "Unknown"
        kind = "unknown"

    return {
        "kind": kind,
        "sport": sport,
        "text": text,
        "subtext": score or status_long,
        "start_time": start_time,
        "status_short": code,
        "status_long": status_long,
        "score": score,
        "home": home,
        "away": away,
        "event_id": event_id(event, sport),
        "confidence": round(confidence, 3),
        "matched": True,
    }


def unmatched_game_metadata(channel: dict[str, Any], reason: str) -> dict[str, Any]:
    sport = SPORT_BY_GROUP.get(channel.get("group", ""))
    return {
        "kind": "unknown",
        "sport": sport,
        "text": "Unknown",
        "subtext": reason,
        "start_time": "",
        "status_short": "",
        "status_long": reason,
        "score": "",
        "home": "",
        "away": "",
        "event_id": None,
        "confidence": 0,
        "matched": False,
    }


def stream_game_metadata() -> dict[str, Any]:
    return {
        "kind": "stream",
        "sport": "",
        "text": "Stream",
        "subtext": "No game lookup",
        "start_time": "",
        "status_short": "",
        "status_long": "No game lookup",
        "score": "",
        "home": "",
        "away": "",
        "event_id": None,
        "confidence": 0,
        "matched": False,
    }


def status_code(event: dict[str, Any], sport: str) -> str:
    return str(event_status(event, sport).get("short") or "").upper()


def select_mma_card_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    config = SPORTS_CONFIG["mma"]

    def rank(event: dict[str, Any]) -> tuple[int, int, int]:
        code = status_code(event, "mma")
        timestamp = int(event.get("timestamp") or 0)
        main_rank = 0 if event.get("is_main") else 1
        if code in config["live_codes"]:
            return 0, main_rank, timestamp
        if code in config["scheduled_codes"]:
            return 1, timestamp, main_rank
        if code in config["inactive_codes"]:
            return 2, timestamp, main_rank
        if code in config["finished_codes"]:
            return 3, main_rank, timestamp
        return 4, timestamp, main_rank

    selected = min(events, key=rank)
    next_event = dict(selected)
    next_event["_sport"] = "mma"
    return next_event


def best_mma_event_match(matchup: dict[str, str], events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    candidates: dict[str, dict[str, Any]] = {}
    for event in events:
        slug = str(event.get("slug") or "").strip()
        first, second = event_team_names(event)
        title_score = team_match_score(matchup.get("title", ""), slug)
        fighter_score = 0.0
        if matchup.get("home") and matchup.get("away"):
            direct = (team_match_score(matchup["home"], first) + team_match_score(matchup["away"], second)) / 2
            swapped = (team_match_score(matchup["home"], second) + team_match_score(matchup["away"], first)) / 2
            fighter_score = max(direct, swapped)
        score = max(title_score, fighter_score)
        key = slug or str(event.get("id") or "")
        if not key:
            continue
        current = candidates.get(key)
        if current is None:
            candidates[key] = {"score": score, "events": [event]}
        else:
            current["score"] = max(current["score"], score)
            current["events"].append(event)

    if not candidates:
        return None, 0.0

    best = max(candidates.values(), key=lambda item: item["score"])
    if best["score"] < 0.78:
        return None, best["score"]
    return select_mma_card_event(best["events"]), best["score"]


def best_event_match(matchup: dict[str, str], events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    if matchup.get("sport") == "mma":
        return best_mma_event_match(matchup, events)

    best_event = None
    best_score = 0.0
    for event in events:
        home, away = event_team_names(event)
        direct = (team_match_score(matchup["home"], home) + team_match_score(matchup["away"], away)) / 2
        swapped = (team_match_score(matchup["home"], away) + team_match_score(matchup["away"], home)) / 2
        score = max(direct, swapped)
        if score > best_score:
            best_event = event
            best_score = score
    if best_score < 0.82:
        return None, best_score
    return best_event, best_score


def enrich_channels_with_games(channels: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cache, meta = ensure_sports_cache_for(channels)
    query_dates = sports_query_dates()
    enriched = []

    for channel in channels:
        next_channel = dict(channel)
        matchup = parse_channel_matchup(channel)
        if matchup is None:
            next_channel["game"] = stream_game_metadata() if channel.get("group") not in SPORT_BY_GROUP else unmatched_game_metadata(channel, "Could not parse matchup")
            enriched.append(next_channel)
            continue

        if not meta["configured"]:
            next_channel["game"] = unmatched_game_metadata(channel, "API key not configured")
            enriched.append(next_channel)
            continue

        sport = matchup["sport"]
        sport_cache = cache.get("sports", {}).get(sport, {})
        events = [
            event
            for date_value in query_dates
            for event in sport_cache.get("dates", {}).get(date_value, {}).get("events", [])
        ]
        event, confidence = best_event_match(matchup, events)
        if event is None:
            next_channel["game"] = unmatched_game_metadata(channel, "No matching game found")
        else:
            next_channel["game"] = game_metadata(event, sport, confidence)
        enriched.append(next_channel)

    return enriched, meta


def parse_extinf(line: str) -> tuple[dict[str, str], str]:
    attrs = {key.lower(): value.strip() for key, value in ATTR_RE.findall(line)}
    display_name = ""
    if "," in line:
        display_name = line.split(",", 1)[1].strip()
    return attrs, display_name


def parse_m3u(content: str, source_id: str) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    pending_attrs: dict[str, str] = {}
    pending_name = ""
    order = 0

    for raw_line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue

        if line.upper().startswith("#EXTINF"):
            pending_attrs, pending_name = parse_extinf(line)
            continue

        if line.startswith("#"):
            continue

        url = line
        name = (
            pending_attrs.get("tvg-name")
            or pending_name
            or pending_attrs.get("name")
            or f"Channel {order + 1}"
        ).strip()
        group = (pending_attrs.get("group-title") or "Ungrouped").strip() or "Ungrouped"
        tvg_id = pending_attrs.get("tvg-id", "").strip()
        logo = pending_attrs.get("tvg-logo", "").strip()
        channel_id = make_id("ch", source_id, tvg_id, name, url)

        channels.append(
            {
                "id": channel_id,
                "source_id": source_id,
                "name": name,
                "group": group,
                "url": url,
                "logo": logo,
                "tvg_id": tvg_id,
                "order": order,
                "status": "live",
                "last_seen": now_ts(),
            }
        )
        order += 1
        pending_attrs = {}
        pending_name = ""

    return channels


def source_filename(source_id: str) -> Path:
    return SOURCE_DIR / f"{source_id}.m3u"


def fetch_playlist_url(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GridPlayer-IPTV/1.0",
            "Accept": "application/x-mpegURL, audio/x-mpegurl, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read()
    return raw.decode("utf-8-sig", errors="replace")


def source_content(source: dict[str, Any]) -> str:
    if source.get("kind") == "url":
        return fetch_playlist_url(source["url"])

    stored_path = Path(source["stored_path"])
    if not stored_path.is_absolute():
        stored_path = APP_DIR / stored_path
    return stored_path.read_text(encoding="utf-8-sig", errors="replace")


def replace_source_channels(state: dict[str, Any], source: dict[str, Any], content: str) -> int:
    channels = parse_m3u(content, source["id"])
    favorite_ids = set(state.get("favorites", []))
    old_channels = state.get("channels", {})

    next_channels = {
        channel_id: channel
        for channel_id, channel in old_channels.items()
        if channel.get("source_id") != source["id"]
    }
    for channel in channels:
        channel["favorite"] = channel["id"] in favorite_ids
        next_channels[channel["id"]] = channel

    state["channels"] = next_channels
    state["queue"] = [channel_id for channel_id in state.get("queue", []) if channel_id in next_channels]
    state["favorites"] = [channel_id for channel_id in state.get("favorites", []) if channel_id in next_channels]

    source["channel_count"] = len(channels)
    source["updated_at"] = now_ts()
    return len(channels)


def upsert_file_source(name: str, content: str) -> dict[str, Any]:
    state = read_state()
    source_id = make_id("src", "file", name, str(now_ts()))
    stored_path = source_filename(source_id)
    stored_path.write_text(content, encoding="utf-8")

    source = {
        "id": source_id,
        "name": name or "Local Playlist",
        "kind": "file",
        "stored_path": str(stored_path.relative_to(APP_DIR)),
        "imported_at": now_ts(),
        "updated_at": now_ts(),
        "channel_count": 0,
    }
    state["sources"].append(source)
    state["selected_source_id"] = source_id
    replace_source_channels(state, source, content)
    write_state(state)
    return source


def upsert_url_source(name: str, url: str, content: str) -> dict[str, Any]:
    state = read_state()
    existing = next((source for source in state["sources"] if source.get("kind") == "url" and source.get("url") == url), None)
    source = existing or {
        "id": make_id("src", "url", url),
        "kind": "url",
        "imported_at": now_ts(),
        "channel_count": 0,
    }
    source["name"] = name or source.get("name") or url
    source["url"] = url
    source["updated_at"] = now_ts()

    if existing is None:
        state["sources"].append(source)
    state["selected_source_id"] = source["id"]
    replace_source_channels(state, source, content)
    write_state(state)
    return source


def common_gridplayer_paths() -> list[Path]:
    candidates = [
        os.environ.get("GRIDPLAYER_PATH", ""),
        r"C:\Program Files\GridPlayer\GridPlayer.exe",
        r"C:\Program Files (x86)\GridPlayer\GridPlayer.exe",
        r"C:\Program Files\gridplayer\GridPlayer.exe",
        r"C:\Program Files (x86)\gridplayer\GridPlayer.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "GridPlayer" / "GridPlayer.exe"),
        str(Path.home() / "AppData" / "Local" / "Programs" / "gridplayer" / "GridPlayer.exe"),
    ]
    return [Path(path) for path in candidates if path]


def find_gridplayer(settings: dict[str, Any]) -> Path | None:
    configured = settings.get("gridplayer_path", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured)

    for candidate in common_gridplayer_paths():
        if candidate.is_file():
            return candidate

    for command in ("GridPlayer", "gridplayer", "GridPlayer.exe", "gridplayer.exe"):
        found = shutil.which(command)
        if found:
            return Path(found)

    return None


def launch_gridplayer(urls: list[str], settings: dict[str, Any]) -> Path:
    executable = find_gridplayer(settings)
    if executable is None:
        raise FileNotFoundError("GridPlayer.exe was not found. Set the path in Settings.")
    if not urls:
        raise ValueError("No stream URLs to open.")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    subprocess.Popen(
        [str(executable), *urls],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return executable


def public_state() -> dict[str, Any]:
    state = read_state()
    settings = state.get("settings", {})
    gridplayer_path = find_gridplayer(settings)
    categories = sorted({channel.get("group") or "Ungrouped" for channel in state["channels"].values()})
    channels, sports_meta = enrich_channels_with_games(list(state["channels"].values()))
    return {
        "sources": state["sources"],
        "channels": channels,
        "favorites": state["favorites"],
        "queue": state["queue"],
        "categories": categories,
        "sports": sports_meta,
        "selected_source_id": state.get("selected_source_id", "all"),
        "settings": settings,
        "gridplayer": {
            "available": gridplayer_path is not None,
            "path": str(gridplayer_path) if gridplayer_path else "",
        },
        "runtime": {
            "desktop": DESKTOP_MODE,
        },
    }


def json_error(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    with state_lock:
        return jsonify({"success": True, "data": public_state()})


@app.post("/api/import-file")
def api_import_file():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return json_error("Choose an M3U file to import.")

    content = uploaded.read().decode("utf-8-sig", errors="replace")
    name = request.form.get("name") or Path(uploaded.filename).stem or "Local Playlist"
    with state_lock:
        source = upsert_file_source(name, content)
        return jsonify({"success": True, "data": {"source": source, "state": public_state()}})


@app.post("/api/import-url")
def api_import_url():
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()
    name = str(payload.get("name", "")).strip()
    if not url.lower().startswith(("http://", "https://")):
        return json_error("Enter a valid http or https playlist URL.")

    try:
        content = fetch_playlist_url(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return json_error(f"Could not fetch playlist: {exc}")

    with state_lock:
        source = upsert_url_source(name, url, content)
        return jsonify({"success": True, "data": {"source": source, "state": public_state()}})


@app.post("/api/refresh")
def api_refresh_all():
    with state_lock:
        state = read_state()
        refreshed = []
        errors = []
        for source in state["sources"]:
            try:
                content = source_content(source)
                count = replace_source_channels(state, source, content)
                refreshed.append({"id": source["id"], "name": source["name"], "count": count})
            except Exception as exc:  # noqa: BLE001 - endpoint should report per-source failures
                errors.append({"id": source.get("id"), "name": source.get("name"), "error": str(exc)})
        write_state(state)
        return jsonify({"success": True, "data": {"refreshed": refreshed, "errors": errors, "state": public_state()}})


@app.post("/api/sources/<source_id>/refresh")
def api_refresh_source(source_id: str):
    with state_lock:
        state = read_state()
        source = next((item for item in state["sources"] if item["id"] == source_id), None)
        if source is None:
            return json_error("Source not found.", 404)
        try:
            content = source_content(source)
            count = replace_source_channels(state, source, content)
            write_state(state)
        except Exception as exc:  # noqa: BLE001
            return json_error(f"Could not refresh source: {exc}")
        return jsonify({"success": True, "data": {"count": count, "state": public_state()}})


@app.delete("/api/sources/<source_id>")
def api_delete_source(source_id: str):
    with state_lock:
        state = read_state()
        source = next((item for item in state["sources"] if item["id"] == source_id), None)
        if source is None:
            return json_error("Source not found.", 404)
        state["sources"] = [item for item in state["sources"] if item["id"] != source_id]
        state["channels"] = {
            channel_id: channel
            for channel_id, channel in state["channels"].items()
            if channel.get("source_id") != source_id
        }
        state["queue"] = [channel_id for channel_id in state["queue"] if channel_id in state["channels"]]
        state["favorites"] = [channel_id for channel_id in state["favorites"] if channel_id in state["channels"]]
        if state.get("selected_source_id") == source_id:
            state["selected_source_id"] = "all"
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/channels/<channel_id>/favorite")
def api_toggle_favorite(channel_id: str):
    with state_lock:
        state = read_state()
        if channel_id not in state["channels"]:
            return json_error("Channel not found.", 404)
        favorites = set(state.get("favorites", []))
        if channel_id in favorites:
            favorites.remove(channel_id)
            state["channels"][channel_id]["favorite"] = False
        else:
            favorites.add(channel_id)
            state["channels"][channel_id]["favorite"] = True
        state["favorites"] = sorted(favorites)
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/queue/add")
def api_queue_add():
    payload = request.get_json(silent=True) or {}
    channel_id = str(payload.get("channel_id", "")).strip()
    with state_lock:
        state = read_state()
        if channel_id not in state["channels"]:
            return json_error("Channel not found.", 404)
        queue = state.get("queue", [])
        if channel_id not in queue:
            if len(queue) >= MAX_QUEUE_ITEMS:
                return json_error(f"Queue is limited to {MAX_QUEUE_ITEMS} streams.")
            queue.append(channel_id)
        state["queue"] = queue
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/queue/remove")
def api_queue_remove():
    payload = request.get_json(silent=True) or {}
    channel_id = str(payload.get("channel_id", "")).strip()
    with state_lock:
        state = read_state()
        state["queue"] = [item for item in state.get("queue", []) if item != channel_id]
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/queue/reorder")
def api_queue_reorder():
    payload = request.get_json(silent=True) or {}
    channel_id = str(payload.get("channel_id", "")).strip()
    direction = str(payload.get("direction", "")).strip()
    with state_lock:
        state = read_state()
        queue = state.get("queue", [])
        if channel_id not in queue:
            return json_error("Channel not in queue.", 404)
        index = queue.index(channel_id)
        target = index - 1 if direction == "up" else index + 1
        if 0 <= target < len(queue):
            queue[index], queue[target] = queue[target], queue[index]
        state["queue"] = queue
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/queue/clear")
def api_queue_clear():
    with state_lock:
        state = read_state()
        state["queue"] = []
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


@app.post("/api/open")
def api_open_channel():
    payload = request.get_json(silent=True) or {}
    channel_id = str(payload.get("channel_id", "")).strip()
    with state_lock:
        state = read_state()
        channel = state["channels"].get(channel_id)
        if channel is None:
            return json_error("Channel not found.", 404)
        try:
            executable = launch_gridplayer([channel["url"]], state["settings"])
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
        return jsonify({"success": True, "data": {"path": str(executable)}})


@app.post("/api/open-queue")
def api_open_queue():
    with state_lock:
        state = read_state()
        urls = [
            state["channels"][channel_id]["url"]
            for channel_id in state.get("queue", [])
            if channel_id in state["channels"]
        ]
        try:
            executable = launch_gridplayer(urls, state["settings"])
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
        return jsonify({"success": True, "data": {"path": str(executable), "count": len(urls)}})


@app.get("/api/export-queue")
def api_export_queue():
    with state_lock:
        state = read_state()
        lines = ["#EXTM3U"]
        for channel_id in state.get("queue", []):
            channel = state["channels"].get(channel_id)
            if not channel:
                continue
            lines.append(f'#EXTINF:-1 tvg-name="{channel["name"]}" group-title="{channel["group"]}",{channel["name"]}')
            lines.append(channel["url"])
        QUEUE_EXPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return send_file(QUEUE_EXPORT_PATH, as_attachment=True, download_name="gridplayer-queue.m3u")


@app.post("/api/settings")
def api_settings():
    payload = request.get_json(silent=True) or {}
    allowed = {"grid_size", "gridplayer_path", "auto_open_queue", "ui_zoom", "ui_sidebar_width"}
    with state_lock:
        state = read_state()
        settings = state.setdefault("settings", default_state()["settings"])
        for key in allowed:
            if key in payload:
                settings[key] = payload[key]
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def serve_app(host: str, port: int) -> None:
    if waitress_serve is not None:
        waitress_serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=False, use_reloader=False)


def main() -> None:
    global DESKTOP_MODE

    parser = argparse.ArgumentParser(description="GridPlayer IPTV desktop client")
    parser.add_argument("--server", action="store_true", help="Run only the Flask server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--debug", action="store_true", help="Enable pywebview debug tools.")
    args = parser.parse_args()

    ensure_data_dirs()
    port = args.port or find_free_port(args.host)
    url = f"http://{args.host}:{port}"

    if args.server or webview is None:
        DESKTOP_MODE = False
        print(f"GridPlayer IPTV running at {url}")
        serve_app(args.host, port)
        return

    DESKTOP_MODE = True
    server_thread = threading.Thread(target=serve_app, args=(args.host, port), daemon=True)
    server_thread.start()
    time.sleep(0.45)

    webview.create_window(
        "GridPlayer IPTV",
        url,
        width=1480,
        height=920,
        min_size=(1100, 720),
        text_select=True,
    )
    webview.start(debug=args.debug)


if __name__ == "__main__":
    main()
