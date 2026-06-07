from __future__ import annotations

import re

APP_NAME = "IPTV Multi Player"
BASE_VERSION = "0.1.0"
GITHUB_OWNER = "jeremygold02"
GITHUB_REPO = "iptv-multi-player"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_API_REPO = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
RELEASE_ASSET_NAME = "IPTV Multi Player.exe"
VERSION_PATTERN = re.compile(r"\d+(?:\.\d+){0,2}")


try:
    from ._version_build import APP_VERSION as BUILD_VERSION
except ImportError:
    BUILD_VERSION = BASE_VERSION


def clean_version(value: str) -> str:
    match = VERSION_PATTERN.search(str(value or ""))
    return match.group(0) if match else BASE_VERSION


APP_VERSION = clean_version(BUILD_VERSION)


def display_version(value: str | None = None) -> str:
    return clean_version(value or APP_VERSION)


def version_parts(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in clean_version(value).split(".")[:3]]
    return tuple((parts + [0, 0, 0])[:3])
