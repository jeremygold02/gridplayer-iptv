from __future__ import annotations

import argparse
import os
import socket
import threading
import time

from iptv_multi_player import config
from iptv_multi_player.state import ensure_data_dirs
from iptv_multi_player.web import app

try:
    from waitress import serve as waitress_serve
except ImportError:  # pragma: no cover
    waitress_serve = None


APP_USER_MODEL_ID = "jeremygold02.IPTVMultiPlayer"


def set_windows_app_user_model_id() -> None:
    if os.name != "nt":
        return

    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id(APP_USER_MODEL_ID)
    except Exception:
        pass


def load_webview():
    try:
        import webview
    except ImportError:  # pragma: no cover - exercised only on machines without pywebview
        return None
    return webview


def window_icon_path() -> str | None:
    icon_path = config.ASSET_DIR / "icon.ico"
    return str(icon_path) if icon_path.is_file() else None


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
    parser = argparse.ArgumentParser(description="IPTV Multi Player desktop client")
    parser.add_argument("--server", action="store_true", help="Run only the Flask server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--debug", action="store_true", help="Enable pywebview debug tools.")
    args = parser.parse_args()

    ensure_data_dirs()
    port = args.port or find_free_port(args.host)
    url = f"http://{args.host}:{port}"

    if args.server:
        config.DESKTOP_MODE = False
        print(f"IPTV Multi Player running at {url}")
        serve_app(args.host, port)
        return

    set_windows_app_user_model_id()
    webview = load_webview()
    if webview is None:
        config.DESKTOP_MODE = False
        print(f"IPTV Multi Player running at {url}")
        serve_app(args.host, port)
        return

    config.DESKTOP_MODE = True
    server_thread = threading.Thread(target=serve_app, args=(args.host, port), daemon=True)
    server_thread.start()
    time.sleep(0.45)

    webview.create_window(
        "IPTV Multi Player",
        url,
        width=1480,
        height=920,
        min_size=(1100, 720),
        text_select=True,
    )
    webview.start(debug=args.debug, icon=window_icon_path())


if __name__ == "__main__":
    main()
