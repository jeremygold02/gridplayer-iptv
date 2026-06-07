from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .config import DEFAULT_PLAYER, PLAYER_CONFIG, PLAYER_ORDER

def normalize_player_id(player_id: Any) -> str:
    player = str(player_id or "").strip().lower()
    return player if player in PLAYER_CONFIG else DEFAULT_PLAYER


def player_label(player_id: str) -> str:
    return PLAYER_CONFIG[normalize_player_id(player_id)]["label"]


def common_player_paths(player_id: str) -> list[Path]:
    config = PLAYER_CONFIG[normalize_player_id(player_id)]
    candidates = [os.environ.get(config["env"], ""), *config["paths"]]
    return [Path(path) for path in candidates if path]


def find_player(player_id: str, settings: dict[str, Any]) -> Path | None:
    player = normalize_player_id(player_id)
    config = PLAYER_CONFIG[player]
    configured = str(settings.get(config["path_key"], "") or "").strip()
    if configured and Path(configured).is_file():
        return Path(configured)

    for candidate in common_player_paths(player):
        if candidate.is_file():
            return candidate

    for command in config["commands"]:
        found = shutil.which(command)
        if found:
            return Path(found)

    return None


def find_gridplayer(settings: dict[str, Any]) -> Path | None:
    return find_player("gridplayer", settings)


def public_players(settings: dict[str, Any]) -> dict[str, Any]:
    selected = normalize_player_id(settings.get("selected_player"))
    items = []
    for player_id in PLAYER_ORDER:
        config = PLAYER_CONFIG[player_id]
        path = find_player(player_id, settings)
        items.append({
            "id": player_id,
            "label": config["label"],
            "available": path is not None,
            "path": str(path) if path else "",
            "configured_path": str(settings.get(config["path_key"], "") or ""),
            "path_key": config["path_key"],
        })
    return {"selected": selected, "items": items}


def launch_player(urls: list[str], settings: dict[str, Any], player_id: Any = None) -> tuple[str, Path]:
    player = normalize_player_id(player_id or settings.get("selected_player"))
    executable = find_player(player, settings)
    if executable is None:
        raise FileNotFoundError(f"{player_label(player)} was not found. Set the path in Settings.")
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
    return player, executable


def launch_gridplayer(urls: list[str], settings: dict[str, Any]) -> Path:
    return launch_player(urls, settings, "gridplayer")[1]
