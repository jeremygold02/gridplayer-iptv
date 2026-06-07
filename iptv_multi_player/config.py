from __future__ import annotations

import re
import sys
from pathlib import Path

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_asset_dir() -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir)
    return Path(__file__).resolve().parent.parent


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
SPORTS_REFRESH_SECONDS = 15 * 60
SPORTS_DAILY_CALL_LIMIT = 100
DESKTOP_MODE = False
DEFAULT_PLAYER = "gridplayer"
API_SPORTS_KEY_NAME = "API_SPORTS_KEY"

PLAYER_ORDER = ("gridplayer", "mpv", "vlc")
PLAYER_CONFIG = {
    "gridplayer": {
        "label": "GridPlayer",
        "path_key": "gridplayer_path",
        "env": "GRIDPLAYER_PATH",
        "commands": ("GridPlayer", "gridplayer", "GridPlayer.exe", "gridplayer.exe"),
        "paths": (
            r"C:\Program Files\GridPlayer\GridPlayer.exe",
            r"C:\Program Files (x86)\GridPlayer\GridPlayer.exe",
            r"C:\Program Files\gridplayer\GridPlayer.exe",
            r"C:\Program Files (x86)\gridplayer\GridPlayer.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "GridPlayer" / "GridPlayer.exe"),
            str(Path.home() / "AppData" / "Local" / "Programs" / "gridplayer" / "GridPlayer.exe"),
        ),
    },
    "mpv": {
        "label": "MPV",
        "path_key": "mpv_path",
        "env": "MPV_PATH",
        "commands": ("mpv", "mpv.exe"),
        "paths": (
            r"C:\Program Files\mpv\mpv.exe",
            r"C:\Program Files (x86)\mpv\mpv.exe",
            str(Path.home() / "scoop" / "apps" / "mpv" / "current" / "mpv.exe"),
            r"C:\ProgramData\scoop\apps\mpv\current\mpv.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "mpv" / "mpv.exe"),
        ),
    },
    "vlc": {
        "label": "VLC",
        "path_key": "vlc_path",
        "env": "VLC_PATH",
        "commands": ("vlc", "vlc.exe"),
        "paths": (
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "VideoLAN" / "VLC" / "vlc.exe"),
        ),
    },
}

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
