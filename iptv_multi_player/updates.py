from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any
import urllib.error
import urllib.request

from .version import (
    APP_VERSION,
    GITHUB_API_REPO,
    GITHUB_REPO_URL,
    RELEASE_ASSET_NAME,
    display_version,
    version_parts,
)


class UpdateError(RuntimeError):
    pass


def can_install_updates() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def github_json(path: str) -> Any:
    request = urllib.request.Request(
        f"{GITHUB_API_REPO}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "IPTV-Multi-Player",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_release() -> dict[str, Any] | None:
    try:
        return github_json("/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def release_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = release.get("assets") or []
    for asset in assets:
        if str(asset.get("name") or "").lower() == RELEASE_ASSET_NAME.lower():
            return asset
    for asset in assets:
        if str(asset.get("name") or "").lower().endswith(".exe"):
            return asset
    return None


def check_for_update() -> dict[str, Any]:
    current_version = display_version()
    release = latest_release()
    if release is None:
        return {
            "current_version": current_version,
            "latest_version": "",
            "update_available": False,
            "can_install": False,
            "release_url": GITHUB_REPO_URL,
            "repo_url": GITHUB_REPO_URL,
            "asset_name": "",
            "message": "No published releases were found.",
        }

    latest_version = display_version(str(release.get("tag_name") or "").strip())
    release_url = str(release.get("html_url") or GITHUB_REPO_URL).strip()
    asset = release_asset(release)
    asset_url = str(asset.get("browser_download_url") or "") if asset else ""
    update_available = version_parts(latest_version) > version_parts(APP_VERSION)
    has_install_asset = bool(asset_url)
    can_install = update_available and has_install_asset and can_install_updates()
    if update_available and has_install_asset:
        message = f"Update available: {latest_version}."
    elif update_available:
        message = f"Update available: {latest_version}, but no Windows exe asset was attached."
    else:
        message = f"You are on the latest published version ({latest_version})."

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "can_install": can_install,
        "release_url": release_url,
        "repo_url": GITHUB_REPO_URL,
        "asset_name": str(asset.get("name") or "") if asset else "",
        "asset_url": asset_url,
        "message": message,
    }


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "IPTV-Multi-Player"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def write_updater_script(script_path: Path, new_exe: Path, target_exe: Path, pid: int) -> None:
    log_path = script_path.with_suffix(".log")
    script_path.write_text(
        "\n".join([
            "@echo off",
            "setlocal EnableExtensions",
            f'set "TARGET={target_exe}"',
            f'set "NEW_EXE={new_exe}"',
            f'set "APP_DIR={target_exe.parent}"',
            f'set "PID={pid}"',
            f'set "LOG={log_path}"',
            'echo Starting IPTV Multi Player update > "%LOG%"',
            ":wait_for_exit",
            'tasklist /FI "PID eq %PID%" /NH | findstr /C:"%PID%" >nul',
            "if not errorlevel 1 (",
            "  timeout /t 1 /nobreak >nul",
            "  goto wait_for_exit",
            ")",
            'copy /Y "%NEW_EXE%" "%TARGET%" >> "%LOG%" 2>&1',
            "if errorlevel 1 exit /b 1",
            'start "" /D "%APP_DIR%" "%TARGET%"',
            'del /F /Q "%NEW_EXE%" >nul 2>&1',
            'del /F /Q "%~f0" >nul 2>&1',
            "",
        ]),
        encoding="utf-8",
    )


def exit_process_later() -> None:
    os._exit(0)


def install_update() -> dict[str, Any]:
    if not can_install_updates():
        raise UpdateError("Self-update is only available in the packaged Windows app.")

    update = check_for_update()
    if not update["update_available"]:
        raise UpdateError("No update is available.")
    if not update.get("asset_url"):
        raise UpdateError("The latest release does not include a Windows exe asset.")

    target_exe = Path(sys.executable).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="iptv_multi_player_update_"))
    new_exe = temp_dir / RELEASE_ASSET_NAME
    script_path = temp_dir / "update.cmd"

    download_file(update["asset_url"], new_exe)
    write_updater_script(script_path, new_exe, target_exe, os.getpid())

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", str(script_path)],
        cwd=str(target_exe.parent),
        creationflags=creationflags,
        close_fds=True,
    )
    threading.Timer(0.8, exit_process_later).start()
    return {
        "message": "Update downloaded. IPTV Multi Player will restart automatically.",
        "latest_version": update["latest_version"],
    }
