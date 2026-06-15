from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

PLAYER_ID_RE = re.compile(r"[^a-z0-9_-]+")
LEGACY_PLAYERS = (
    ("gridplayer", "gridplayer_path", ""),
    ("mpv", "mpv_path", "mpv_flags"),
    ("vlc", "vlc_path", "vlc_flags"),
)


def player_name_from_path(path: Any) -> str:
    name = Path(str(path or "").strip()).stem.strip()
    return name or "Player"


def clean_player_id(value: Any) -> str:
    player_id = PLAYER_ID_RE.sub("-", str(value or "").strip().lower()).strip("-_")
    return player_id[:80]


def generated_player_id(name: str, path: str, fallback: str = "player") -> str:
    stem = clean_player_id(name) or clean_player_id(player_name_from_path(path)) or fallback
    digest = hashlib.sha1(f"{name}|{path}".encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{stem}-{digest}"


def is_executable_path(path: str) -> bool:
    return bool(path) and Path(path).suffix.lower() == ".exe"


def player_from_payload(item: Any, existing_ids: set[str]) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None

    path = str(item.get("path") or item.get("configured_path") or "").strip()
    name = str(item.get("name") or item.get("label") or "").strip() or player_name_from_path(path)
    flags = str(item.get("flags") or item.get("configured_flags") or "").strip()[:2000]
    if not name or not is_executable_path(path):
        return None

    player_id = clean_player_id(item.get("id")) or generated_player_id(name, path)
    if player_id in existing_ids:
        player_id = generated_player_id(name, path)
    suffix = 2
    base_id = player_id
    while player_id in existing_ids:
        player_id = f"{base_id}-{suffix}"
        suffix += 1

    existing_ids.add(player_id)
    return {
        "id": player_id,
        "name": name[:80],
        "path": path,
        "flags": flags,
    }


def sanitize_players(value: Any) -> list[dict[str, str]]:
    players: list[dict[str, str]] = []
    existing_ids: set[str] = set()
    raw_players = value if isinstance(value, list) else []
    for item in raw_players:
        player = player_from_payload(item, existing_ids)
        if player:
            players.append(player)
    return players


def legacy_players_from_settings(settings: dict[str, Any]) -> list[dict[str, str]]:
    players: list[dict[str, str]] = []
    existing_ids: set[str] = set()
    for legacy_id, path_key, flags_key in LEGACY_PLAYERS:
        path = str(settings.get(path_key) or "").strip()
        if not path:
            continue
        player = player_from_payload(
            {
                "name": player_name_from_path(path),
                "path": path,
                "flags": settings.get(flags_key, "") if flags_key else "",
            },
            existing_ids,
        )
        if player:
            player["legacy_id"] = legacy_id
            players.append(player)
    return players


def normalize_player_settings(settings: dict[str, Any]) -> dict[str, Any]:
    next_settings = dict(settings)
    has_player_list = isinstance(next_settings.get("players"), list)
    players = sanitize_players(next_settings.get("players"))
    legacy_players = legacy_players_from_settings(next_settings)

    if not has_player_list and legacy_players:
        players = legacy_players
    next_settings["players"] = [
        {key: value for key, value in player.items() if key != "legacy_id"}
        for player in players
    ]

    selected = ""
    if not has_player_list and legacy_players:
        legacy_selected = str(next_settings.get("selected_player") or "").strip().lower()
        selected = next(
            (player["id"] for player in legacy_players if player.get("legacy_id") == legacy_selected),
            "",
        )
    if not selected:
        selected = normalize_player_id(next_settings.get("selected_player"), {"players": players})
    if not selected and players:
        selected = players[0]["id"]
    next_settings["selected_player"] = selected
    return next_settings


def player_items(settings: dict[str, Any]) -> list[dict[str, str]]:
    return sanitize_players(settings.get("players"))


def normalize_player_id(player_id: Any, settings: dict[str, Any] | None = None) -> str:
    requested = str(player_id or "").strip()
    if not settings:
        return clean_player_id(requested)

    players = player_items(settings)
    if any(player["id"] == requested for player in players):
        return requested
    return players[0]["id"] if players else ""


def player_by_id(player_id: Any, settings: dict[str, Any]) -> dict[str, str] | None:
    normalized = normalize_player_id(player_id, settings)
    if not normalized:
        return None
    return next((player for player in player_items(settings) if player["id"] == normalized), None)


def player_label(player_id: Any, settings: dict[str, Any] | None = None) -> str:
    if settings:
        player = player_by_id(player_id, settings)
        if player:
            return player["name"]
    return str(player_id or "Player")


def find_player(player_id: Any, settings: dict[str, Any]) -> Path | None:
    player = player_by_id(player_id, settings)
    if not player:
        return None
    path = Path(player["path"])
    return path if path.is_file() else None


def player_flags(player_id: Any, settings: dict[str, Any]) -> str:
    player = player_by_id(player_id, settings)
    return str(player.get("flags", "") if player else "").strip()


def player_flag_args(player_id: Any, settings: dict[str, Any]) -> list[str]:
    flags = player_flags(player_id, settings)
    if not flags:
        return []
    try:
        return shlex.split(flags)
    except ValueError as exc:
        raise ValueError(f"Could not parse {player_label(player_id, settings)} flags: {exc}") from exc


def public_players(settings: dict[str, Any]) -> dict[str, Any]:
    normalized_settings = normalize_player_settings(settings)
    selected = normalize_player_id(normalized_settings.get("selected_player"), normalized_settings)
    items = []
    for player in player_items(normalized_settings):
        path = Path(player["path"])
        available = path.is_file()
        items.append({
            "id": player["id"],
            "label": player["name"],
            "name": player["name"],
            "available": available,
            "path": str(path) if available else "",
            "configured_path": player["path"],
            "configured_flags": player.get("flags", ""),
            "supports_flags": True,
        })
    return {"selected": selected, "items": items}


def launch_player(urls: list[str], settings: dict[str, Any], player_id: Any = None) -> tuple[str, Path]:
    normalized_settings = normalize_player_settings(settings)
    player = normalize_player_id(player_id or normalized_settings.get("selected_player"), normalized_settings)
    if not player:
        raise FileNotFoundError("No player is configured. Add a player in Settings.")

    executable = find_player(player, normalized_settings)
    if executable is None:
        raise FileNotFoundError(f"{player_label(player, normalized_settings)} was not found. Set the path in Settings.")
    if not urls:
        raise ValueError("No stream URLs to open.")

    flag_args = player_flag_args(player, normalized_settings)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    subprocess.Popen(
        [str(executable), *flag_args, *urls],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return player, executable
