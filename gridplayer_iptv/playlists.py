from __future__ import annotations

from pathlib import Path
from typing import Any
import urllib.request

from .config import ATTR_RE, HTTP_TIMEOUT_SECONDS, SOURCE_DIR
from .state import ensure_data_dirs, make_id, now_ts, read_state, write_state

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
