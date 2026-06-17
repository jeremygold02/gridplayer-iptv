from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
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


GITHUB_API_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 256 * 1024
UPDATE_PROGRESS_IDLE = {
    "phase": "idle",
    "message": "",
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "bytes_per_second": 0,
    "active": False,
}
update_progress_lock = threading.Lock()
update_progress_state = dict(UPDATE_PROGRESS_IDLE)


def set_update_progress(**values: Any) -> None:
    with update_progress_lock:
        update_progress_state.update(values)
        phase = update_progress_state.get("phase")
        update_progress_state["active"] = phase not in {"idle", "complete", "error"}


def reset_update_progress() -> None:
    with update_progress_lock:
        update_progress_state.clear()
        update_progress_state.update(UPDATE_PROGRESS_IDLE)


def update_progress() -> dict[str, Any]:
    with update_progress_lock:
        return dict(update_progress_state)


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
    try:
        with urllib.request.urlopen(request, timeout=GITHUB_API_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise UpdateError("GitHub took too long to respond. Try again in a moment.") from exc


def latest_release() -> dict[str, Any] | None:
    try:
        return github_json("/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def release_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = release.get("assets") or []
    expected_stem = comparable_exe_stem(RELEASE_ASSET_NAME)
    for asset in assets:
        if comparable_exe_stem(str(asset.get("name") or "")) == expected_stem:
            return asset
    return None


def comparable_exe_stem(name: str) -> str:
    path = Path(name)
    if path.suffix.lower() != ".exe":
        return ""
    return "".join(character for character in path.stem.lower() if character.isalnum())


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
        try:
            total_bytes = int(response.headers.get("Content-Length") or 0)
        except ValueError:
            total_bytes = 0
        downloaded_bytes = 0
        started_at = time.monotonic()
        set_update_progress(
            phase="downloading",
            message="Downloading update...",
            downloaded_bytes=0,
            total_bytes=total_bytes,
            bytes_per_second=0,
        )
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)
            downloaded_bytes += len(chunk)
            elapsed = max(time.monotonic() - started_at, 0.001)
            set_update_progress(
                phase="downloading",
                message="Downloading update...",
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                bytes_per_second=downloaded_bytes / elapsed,
            )


def powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def updater_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key == "_MEIPASS2" or key.startswith("_PYI_"):
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def write_updater_script(script_path: Path, new_exe: Path, target_exe: Path, pid: int) -> None:
    log_path = script_path.with_suffix(".log")
    script_path.write_text(
        "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$target = {powershell_literal(target_exe)}",
            f"$newExe = {powershell_literal(new_exe)}",
            f"$appDir = {powershell_literal(target_exe.parent)}",
            f"$pidToWait = {pid}",
            f"$log = {powershell_literal(log_path)}",
            "",
            "function Write-UpdateLog {",
            "  param([string]$Message)",
            "  Add-Content -LiteralPath $log -Value \"$(Get-Date -Format o) $Message\"",
            "}",
            "",
            "function Get-AppProcesses {",
            "  @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $target })",
            "}",
            "",
            "Set-Content -LiteralPath $log -Value \"$(Get-Date -Format o) Starting IPTV Multi Player update\"",
            "try {",
            "  $deadline = (Get-Date).AddSeconds(20)",
            "  while ((Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {",
            "    Start-Sleep -Milliseconds 500",
            "  }",
            "",
            "  $remaining = @(Get-AppProcesses)",
            "  if ($remaining.Count -gt 0) {",
            "    Write-UpdateLog \"Stopping $($remaining.Count) old app process(es).\"",
            "    foreach ($process in $remaining) {",
            "      Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue",
            "    }",
            "    Start-Sleep -Milliseconds 800",
            "  }",
            "",
            "  $copied = $false",
            "  for ($attempt = 1; $attempt -le 60; $attempt++) {",
            "    try {",
            "      Copy-Item -LiteralPath $newExe -Destination $target -Force -ErrorAction Stop",
            "      $copied = $true",
            "      break",
            "    } catch {",
            "      Write-UpdateLog \"Copy attempt $attempt failed: $($_.Exception.Message)\"",
            "      Start-Sleep -Seconds 1",
            "    }",
            "  }",
            "  if (-not $copied) {",
            "    throw 'Could not replace the app after 60 attempts.'",
            "  }",
            "",
            "  Get-ChildItem Env: | Where-Object { $_.Name -eq '_MEIPASS2' -or $_.Name -like '_PYI_*' } | Remove-Item",
            "  $env:PYINSTALLER_RESET_ENVIRONMENT = '1'",
            "  Write-UpdateLog 'Starting updated app.'",
            "  Start-Process -FilePath $target -WorkingDirectory $appDir",
            "  Remove-Item -LiteralPath $newExe -Force -ErrorAction SilentlyContinue",
            "  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
            "} catch {",
            "  Write-UpdateLog \"Update failed: $($_.Exception.Message)\"",
            "  exit 1",
            "}",
            "",
        ]),
        encoding="utf-8",
    )


def exit_process_later() -> None:
    os._exit(0)


def install_update() -> dict[str, Any]:
    reset_update_progress()
    set_update_progress(phase="checking", message="Checking for update...")
    if not can_install_updates():
        set_update_progress(phase="error", message="Self-update is only available in the packaged Windows app.")
        raise UpdateError("Self-update is only available in the packaged Windows app.")

    try:
        update = check_for_update()
        if not update["update_available"]:
            raise UpdateError("No update is available.")
        if not update.get("asset_url"):
            raise UpdateError("The latest release does not include a Windows exe asset.")

        target_exe = Path(sys.executable).resolve()
        temp_dir = Path(tempfile.mkdtemp(prefix="iptv_multi_player_update_"))
        new_exe = temp_dir / RELEASE_ASSET_NAME
        script_path = temp_dir / "update.ps1"

        download_file(update["asset_url"], new_exe)
        set_update_progress(phase="installing", message="Download complete. Preparing restart...")
        write_updater_script(script_path, new_exe, target_exe, os.getpid())

        creationflags = 0
        startupinfo = None
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script_path),
            ],
            cwd=str(target_exe.parent),
            creationflags=creationflags,
            env=updater_environment(),
            startupinfo=startupinfo,
            close_fds=True,
        )
        message = "Update downloaded. IPTV Multi Player will restart automatically."
        set_update_progress(phase="complete", message=message)
        threading.Timer(0.8, exit_process_later).start()
        return {
            "message": message,
            "latest_version": update["latest_version"],
        }
    except Exception as exc:
        set_update_progress(phase="error", message=str(exc))
        raise
