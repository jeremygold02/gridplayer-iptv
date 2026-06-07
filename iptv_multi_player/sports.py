from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import json
import re
import unicodedata
import urllib.request
from urllib.parse import urlencode
from typing import Any

from .config import (
    HTTP_TIMEOUT_SECONDS,
    MATCHUP_RE,
    SPORTS_CONFIG,
    SPORT_BY_GROUP,
    SPORTS_DAILY_CALL_LIMIT,
    SPORTS_REFRESH_SECONDS,
    TEAM_ALIASES,
)
from .state import (
    api_sports_key,
    local_date,
    local_time_text,
    now_ts,
    read_sports_cache,
    sports_query_dates,
    write_sports_cache,
)

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
    if SPORTS_CONFIG[sport].get("event_kind") == "race":
        return {
            "sport": sport,
            "home": "",
            "away": "",
            "title": title,
        }

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


def parse_event_timestamp(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        pass

    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    return 0


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
            "User-Agent": "IPTV-Multi-Player/1.0",
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
    if event.get("_sport") == "formula_1":
        return event_title(event, "formula_1"), ""

    if event.get("_sport") == "mma" or "fighters" in event:
        fighters = event.get("fighters") or {}
        first = fighters.get("first") or {}
        second = fighters.get("second") or {}
        return str(first.get("name") or ""), str(second.get("name") or "")

    teams = event.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or teams.get("visitors") or {}
    return str(home.get("name") or ""), str(away.get("name") or "")


def event_title(event: dict[str, Any], sport: str) -> str:
    if sport == "formula_1":
        competition = event.get("competition") or {}
        race_type = str(event.get("type") or "").strip()
        name = str(competition.get("name") or event.get("name") or "").strip()
        if name and race_type and race_type.lower() not in name.lower():
            return f"{name} {race_type}"
        return name or race_type or "Formula 1"
    if sport == "mma":
        return str(event.get("slug") or "").strip()
    home, away = event_team_names(event)
    return f"{home} vs {away}".strip()


def event_status(event: dict[str, Any], sport: str) -> dict[str, Any]:
    if sport == "football":
        status = ((event.get("fixture") or {}).get("status") or {})
    elif sport == "mma":
        status = event.get("status") or {}
    else:
        status = event.get("status") or {}
    if isinstance(status, dict):
        return status
    if status in (None, ""):
        return {}
    return {"short": str(status), "long": str(status)}


def event_timestamp(event: dict[str, Any], sport: str) -> Any:
    if sport == "football":
        return parse_event_timestamp((event.get("fixture") or {}).get("timestamp"))
    if sport == "mma":
        return parse_event_timestamp(event.get("timestamp") or event.get("date"))
    if sport == "nba":
        return parse_event_timestamp((event.get("date") or {}).get("start"))
    return parse_event_timestamp(event.get("timestamp") or event.get("date"))


def event_id(event: dict[str, Any], sport: str) -> Any:
    if sport == "football":
        return (event.get("fixture") or {}).get("id")
    if sport == "mma":
        return event.get("id")
    return event.get("id")


def event_score(event: dict[str, Any], sport: str) -> str:
    if sport in {"formula_1", "mma"}:
        return ""

    if sport == "football":
        goals = event.get("goals") or {}
        home = goals.get("home")
        away = goals.get("away")
    else:
        scores = event.get("scores") or {}
        home = score_value(scores.get("home"))
        away = score_value(scores.get("away") if "away" in scores else scores.get("visitors"))

    if home is None or away is None:
        return ""
    return f"{home}-{away}"


def score_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("total", "points", "score", "goals"):
            if value.get(key) is not None:
                return value.get(key)
        linescore = value.get("linescore")
        if isinstance(linescore, list):
            try:
                return sum(int(item) for item in linescore if str(item).strip())
            except (TypeError, ValueError):
                return None
        return None
    return value


def game_metadata(event: dict[str, Any], sport: str, confidence: float) -> dict[str, Any]:
    config = SPORTS_CONFIG[sport]
    status = event_status(event, sport)
    code = str(status.get("short") or "").upper()
    status_long = str(status.get("long") or config.get("status_labels", {}).get(code) or code or "Unknown")
    elapsed = status.get("elapsed")
    timer = status.get("timer")
    clock = status.get("clock")
    score = event_score(event, sport)
    raw_timestamp = event_timestamp(event, sport)
    start_time = local_time_text(raw_timestamp) if raw_timestamp else ""
    home, away = event_team_names(event)
    start_timestamp = parse_event_timestamp(raw_timestamp) if raw_timestamp else 0

    if code in config["live_codes"]:
        if sport == "mma":
            text = "Live" if code == "LIVE" else status_long
        elif sport == "formula_1":
            text = status_long
        else:
            detail = str(clock or timer or elapsed or code)
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

    display_score = "" if kind == "scheduled" and score in {"0-0", "0.0-0.0"} else score

    return {
        "kind": kind,
        "sport": sport,
        "text": text,
        "subtext": display_score or status_long,
        "start_time": start_time,
        "status_short": code,
        "status_long": status_long,
        "score": display_score,
        "start_timestamp": start_timestamp,
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
        "start_timestamp": 0,
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
        "start_timestamp": 0,
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


def best_title_event_match(matchup: dict[str, str], events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best_event = None
    best_score = 0.0
    for event in events:
        sport = matchup["sport"]
        title = event_title(event, sport)
        score = team_match_score(matchup.get("title", ""), title)
        if score > best_score:
            best_event = event
            best_score = score

    if best_score < 0.55:
        return None, best_score
    return best_event, best_score


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
    if SPORTS_CONFIG[matchup["sport"]].get("event_kind") == "race":
        return best_title_event_match(matchup, events)

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
