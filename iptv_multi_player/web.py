from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

from . import config
from .config import API_SPORTS_KEY_NAME, ASSET_DIR, MAX_QUEUE_ITEMS, QUEUE_EXPORT_PATH
from .players import launch_player, normalize_player_id, player_label, public_players
from .playlists import (
    fetch_playlist_url,
    replace_source_channels,
    source_content,
    upsert_file_source,
    upsert_url_source,
)
from .sports import channels_with_pending_games, enrich_channels_with_games
from .state import api_sports_key, default_state, read_state, write_env_value, write_state
from .updates import UpdateError, can_install_updates, check_for_update, install_update
from .version import APP_NAME, APP_VERSION, GITHUB_REPO_URL

app = Flask(
    __name__,
    template_folder=str(ASSET_DIR / "templates"),
    static_folder=str(ASSET_DIR / "static"),
)
state_lock = threading.RLock()


def public_state(state: dict[str, Any] | None = None, enrich_games: bool = False) -> dict[str, Any]:
    state = state or read_state()
    settings = state.get("settings", {})
    sports_key = api_sports_key()
    players = public_players(settings)
    gridplayer = next((player for player in players["items"] if player["id"] == "gridplayer"), None)
    categories = sorted({channel.get("group") or "Ungrouped" for channel in state["channels"].values()})
    channel_values = list(state["channels"].values())
    if enrich_games:
        channels, sports_meta = enrich_channels_with_games(channel_values)
    else:
        channels, sports_meta = channels_with_pending_games(channel_values)
    return {
        "sources": state["sources"],
        "channels": channels,
        "favorites": state["favorites"],
        "queue": state["queue"],
        "categories": categories,
        "sports": sports_meta,
        "selected_source_id": state.get("selected_source_id", "all"),
        "settings": settings,
        "players": players,
        "api_sports": {
            "key": sports_key,
            "configured": bool(sports_key),
        },
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "repo_url": GITHUB_REPO_URL,
        },
        "gridplayer": {
            "available": bool(gridplayer and gridplayer["available"]),
            "path": gridplayer["path"] if gridplayer else "",
        },
        "runtime": {
            "desktop": config.DESKTOP_MODE,
            "can_install_updates": can_install_updates(),
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


@app.post("/api/sports/refresh")
def api_sports_refresh():
    try:
        with state_lock:
            state = read_state()
        return jsonify({"success": True, "data": public_state(state=state, enrich_games=True)})
    except Exception as exc:  # noqa: BLE001 - game enrichment should not crash the app shell
        return json_error(f"Could not refresh game data: {exc}", 502)


@app.get("/api/update/check")
@app.get("/api/update-check")
def api_update_check():
    try:
        return jsonify({"success": True, "data": check_for_update()})
    except Exception as exc:  # noqa: BLE001 - UI should get a concise failure message
        return json_error(f"Could not check for updates: {exc}", 502)


@app.post("/api/update/install")
def api_update_install():
    try:
        return jsonify({"success": True, "data": install_update()})
    except UpdateError as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        return json_error(f"Could not install update: {exc}", 502)


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
            player, executable = launch_player([channel["url"]], state["settings"], payload.get("player"))
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
        return jsonify({"success": True, "data": {"path": str(executable), "player": player, "label": player_label(player)}})


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
            player, executable = launch_player(urls, state["settings"])
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
        return jsonify({"success": True, "data": {"path": str(executable), "player": player, "label": player_label(player), "count": len(urls)}})


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
    return send_file(QUEUE_EXPORT_PATH, as_attachment=True, download_name="iptv-multi-player-queue.m3u")


@app.post("/api/settings")
def api_settings():
    payload = request.get_json(silent=True) or {}
    allowed = {
        "grid_size",
        "selected_player",
        "gridplayer_path",
        "mpv_path",
        "vlc_path",
        "auto_open_queue",
        "ui_zoom",
        "ui_sidebar_width",
    }
    with state_lock:
        state = read_state()
        settings = state.setdefault("settings", default_state()["settings"])
        if "api_sports_key" in payload:
            write_env_value(API_SPORTS_KEY_NAME, str(payload.get("api_sports_key") or ""))
        for key in allowed:
            if key in payload:
                if key == "selected_player":
                    settings[key] = normalize_player_id(payload[key])
                elif key.endswith("_path"):
                    settings[key] = str(payload[key] or "").strip()
                else:
                    settings[key] = payload[key]
        write_state(state)
        return jsonify({"success": True, "data": {"state": public_state()}})
