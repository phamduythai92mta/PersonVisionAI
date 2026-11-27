"""Portable path resolution for bundled resources and persistent application data."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "VideoPersonDetector"
APP_DISPLAY_NAME = "AI Phát Hiện Người Trong Video"


def is_frozen() -> bool:
    """Return True when running from a PyInstaller frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_resource_dir() -> Path:
    """Return the root directory containing bundled read-only assets (web, models, assets)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_executable_dir() -> Path:
    """Return the folder containing the running executable (or project root in dev mode)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _is_dir_writable(target: Path) -> bool:
    """Check whether we can create and write a file inside target directory."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def resolve_data_dir() -> Path:
    """
    Resolve persistent data directory.

    Prefers a portable `data/` directory beside the executable so the entire packaged
    folder can be copied to another Windows PC along with its history and snapshots.
    Falls back to `%LOCALAPPDATA%\\VideoPersonDetector\\data` if the app directory is read-only.
    """
    env_override = os.environ.get("VPD_DATA_DIR")
    if env_override:
        custom = Path(env_override).expanduser().resolve()
        custom.mkdir(parents=True, exist_ok=True)
        return custom

    portable_data = get_executable_dir() / "data"
    if _is_dir_writable(portable_data):
        return portable_data

    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    fallback = local_appdata / APP_NAME / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


@dataclass(frozen=True)
class AppPaths:
    resource_dir: Path
    exe_dir: Path
    data_dir: Path
    db_path: Path
    snapshots_dir: Path
    cache_dir: Path
    web_dir: Path
    model_path: Path
    icon_path: Path
    portable_mode: bool


def get_app_paths() -> AppPaths:
    """Build and ensure all application directories exist."""
    resource_dir = get_resource_dir()
    exe_dir = get_executable_dir()
    data_dir = resolve_data_dir()
    snapshots_dir = data_dir / "snapshots"
    cache_dir = data_dir / "cache"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    portable_mode = data_dir == (exe_dir / "data")

    return AppPaths(
        resource_dir=resource_dir,
        exe_dir=exe_dir,
        data_dir=data_dir,
        db_path=data_dir / "history.db",
        snapshots_dir=snapshots_dir,
        cache_dir=cache_dir,
        web_dir=resource_dir / "web",
        model_path=resource_dir / "models" / "yolo11n.onnx",
        icon_path=resource_dir / "assets" / "icon.ico",
        portable_mode=portable_mode,
    )
