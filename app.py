from __future__ import annotations

import argparse
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
from pathlib import Path
from typing import Any

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

MAX_QUEUE_ITEMS = 16
HTTP_TIMEOUT_SECONDS = 18
DESKTOP_MODE = False

ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')

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


def make_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def now_ts() -> int:
    return int(time.time())


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
    return {
        "sources": state["sources"],
        "channels": list(state["channels"].values()),
        "favorites": state["favorites"],
        "queue": state["queue"],
        "categories": categories,
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
