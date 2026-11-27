"""Desktop entry point for PersonVision AI."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

# PyInstaller GUI executables (`console=False`) set sys.stdout/sys.stderr to None,
# which crashes libraries like uvicorn that call `.isatty()`.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn

from backend.api import create_app
from backend.paths import APP_DISPLAY_NAME, get_app_paths


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(url: str, timeout_sec: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.12)
    return False


def _launch_browser_app_fallback(url: str) -> None:
    """Open Edge/Chrome in standalone app window mode if WebView2 runtime is absent."""
    edge_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in edge_candidates:
        if exe.is_file():
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1320,860"])
            return
    if shutil.which("msedge"):
        subprocess.Popen(["msedge", f"--app={url}", "--window-size=1320,860"])
        return
    webbrowser.open(url)


def main() -> None:
    paths = get_app_paths()
    window_holder: dict[str, Any] = {"window": None}
    app = create_app(paths=paths, window_holder=window_holder)

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not _wait_for_server(f"{base_url}/api/app-info"):
        print("Failed to start internal local server.", file=sys.stderr)
        sys.exit(1)

    headless_server = os.environ.get("VPD_HEADLESS") == "1"
    if headless_server:
        print(f"Server running at {base_url}")
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return

    try:
        import webview

        window = webview.create_window(
            title=APP_DISPLAY_NAME,
            url=base_url,
            width=1360,
            height=860,
            min_size=(1020, 680),
            background_color="#0b1120",
            text_select=True,
        )
        window_holder["window"] = window
        icon_arg = str(paths.icon_path) if paths.icon_path.is_file() else None
        try:
            webview.start(gui="edgechromium", icon=icon_arg, debug=False)
        except TypeError:
            webview.start(gui="edgechromium", debug=False)
    except Exception:
        _launch_browser_app_fallback(base_url)
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
