"""FastAPI routes connecting the UI, folder scanner, history database, and video streaming."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import Database
from .paths import AppPaths
from .scanner import ScannerManager, discover_videos
from .video_service import VideoPlaybackManager, needs_transcode, stream_video_range


class ScanStartPayload(BaseModel):
    folder_path: str
    recursive: bool = True
    sample_interval_sec: float = Field(default=1.0, ge=0.2, le=30.0)
    confidence_threshold: float = Field(default=0.45, ge=0.15, le=0.95)
    merge_gap_sec: float = Field(default=3.0, ge=0.0, le=60.0)
    max_segment_sec: float = Field(default=15.0, ge=2.0, le=120.0)
    skip_scanned: bool = True


class SettingsPayload(BaseModel):
    sample_interval_sec: float | None = None
    confidence_threshold: float | None = None
    merge_gap_sec: float | None = None
    max_segment_sec: float | None = None
    recursive: bool | None = None
    skip_scanned: bool | None = None


class RelocateVideoPayload(BaseModel):
    new_path: str


def _pick_folder_tk(initial_dir: str = "") -> str | None:
    """Open native Windows folder picker dialog using Tkinter on a helper call."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(
            title="Chọn thư mục chứa video để quét",
            initialdir=initial_dir if initial_dir and Path(initial_dir).is_dir() else None,
        )
        return chosen or None
    finally:
        root.destroy()


def _pick_video_file_tk(initial_dir: str = "") -> str | None:
    """Open native Windows file picker dialog to relocate a moved video."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askopenfilename(
            title="Chọn lại file video đã di chuyển",
            initialdir=initial_dir if initial_dir and Path(initial_dir).is_dir() else None,
            filetypes=[
                ("Video files", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v *.ts *.mpg *.mpeg"),
                ("All files", "*.*"),
            ],
        )
        return chosen or None
    finally:
        root.destroy()


def create_app(paths: AppPaths, window_holder: dict[str, Any] | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    db = Database(paths.db_path)
    scanner = ScannerManager(paths=paths, db=db)
    playback = VideoPlaybackManager(cache_dir=paths.cache_dir)

    app = FastAPI(title="AI Phát Hiện Người Trong Video")

    app.state.paths = paths
    app.state.db = db
    app.state.scanner = scanner
    app.state.playback = playback

    app.mount("/static", StaticFiles(directory=str(paths.web_dir)), name="static")
    app.mount("/snapshots", StaticFiles(directory=str(paths.snapshots_dir)), name="snapshots")

    @app.get("/", response_class=HTMLResponse)
    def serve_index() -> FileResponse:
        return FileResponse(paths.web_dir / "index.html")

    @app.get("/api/app-info")
    def get_app_info() -> dict[str, Any]:
        return {
            "portable_mode": paths.portable_mode,
            "data_dir": str(paths.data_dir),
            "exe_dir": str(paths.exe_dir),
            "model_ready": paths.model_path.is_file(),
            "stats": db.get_summary_stats(),
            "settings": db.get_settings(),
        }

    # ------------------------------------------------------------------
    # Native Dialogs
    # ------------------------------------------------------------------
    @app.post("/api/dialog/folder")
    def pick_folder(initial_dir: str = "") -> dict[str, Any]:
        win = window_holder.get("window") if window_holder else None
        chosen: str | None = None
        if win is not None:
            try:
                import webview

                result = win.create_file_dialog(
                    webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG,
                    directory=initial_dir if initial_dir and Path(initial_dir).is_dir() else "",
                )
                if result and len(result) > 0:
                    chosen = str(result[0])
            except Exception:
                chosen = None

        if not chosen:
            # Fallback to Tk dialog (also runs cleanly when in browser fallback mode)
            done = threading.Event()
            holder: dict[str, str | None] = {"path": None}

            def _run() -> None:
                holder["path"] = _pick_folder_tk(initial_dir)
                done.set()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            done.wait(timeout=120)
            chosen = holder["path"]

        if not chosen:
            return {"selected": False, "folder_path": None}

        folder = Path(chosen)
        vids = discover_videos(folder, recursive=True)
        already_names = db.get_already_scanned_names()
        skipped_cnt = sum(1 for v in vids if v.name.strip().lower() in already_names)
        return {
            "selected": True,
            "folder_path": str(folder),
            "video_count_recursive": len(vids),
            "already_scanned_count": skipped_cnt,
            "new_count": len(vids) - skipped_cnt,
        }

    @app.post("/api/dialog/video-file")
    def pick_video_file(initial_dir: str = "") -> dict[str, Any]:
        win = window_holder.get("window") if window_holder else None
        chosen: str | None = None
        if win is not None:
            try:
                import webview

                result = win.create_file_dialog(
                    webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG,
                    directory=initial_dir if initial_dir and Path(initial_dir).is_dir() else "",
                    allow_multiple=False,
                    file_types=("Video files (*.mp4;*.avi;*.mov;*.mkv;*.wmv;*.flv;*.webm;*.m4v)", "All files (*.*)"),
                )
                if result and len(result) > 0:
                    chosen = str(result[0])
            except Exception:
                chosen = None

        if not chosen:
            done = threading.Event()
            holder: dict[str, str | None] = {"path": None}

            def _run() -> None:
                holder["path"] = _pick_video_file_tk(initial_dir)
                done.set()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            done.wait(timeout=120)
            chosen = holder["path"]

        return {"selected": bool(chosen), "file_path": chosen}

    @app.get("/api/folder/preview")
    def preview_folder(folder_path: str = Query(...), recursive: bool = True) -> dict[str, Any]:
        folder = Path(folder_path).expanduser()
        if not folder.is_dir():
            return {
                "valid": False,
                "count": 0,
                "new_count": 0,
                "already_scanned_count": 0,
                "sample": [],
                "skipped_sample": [],
            }
        vids = discover_videos(folder, recursive=recursive)
        already_names = db.get_already_scanned_names()
        new_videos = [v for v in vids if v.name.strip().lower() not in already_names]
        skipped_videos = [v for v in vids if v.name.strip().lower() in already_names]
        return {
            "valid": True,
            "count": len(vids),
            "new_count": len(new_videos),
            "already_scanned_count": len(skipped_videos),
            "sample": [v.name for v in new_videos[:6]],
            "skipped_sample": [v.name for v in skipped_videos[:6]],
        }

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return db.get_settings()

    @app.put("/api/settings")
    def save_settings(payload: SettingsPayload) -> dict[str, Any]:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        return db.update_settings(updates)

    # ------------------------------------------------------------------
    # Scanner Controls
    # ------------------------------------------------------------------
    @app.post("/api/scan/start")
    def start_scan(payload: ScanStartPayload) -> dict[str, Any]:
        db.update_settings(
            {
                "recursive": payload.recursive,
                "sample_interval_sec": payload.sample_interval_sec,
                "confidence_threshold": payload.confidence_threshold,
                "merge_gap_sec": payload.merge_gap_sec,
                "max_segment_sec": payload.max_segment_sec,
                "skip_scanned": payload.skip_scanned,
            }
        )
        try:
            return scanner.start_scan(
                folder_path=payload.folder_path,
                recursive=payload.recursive,
                sample_interval_sec=payload.sample_interval_sec,
                confidence_threshold=payload.confidence_threshold,
                merge_gap_sec=payload.merge_gap_sec,
                max_segment_sec=payload.max_segment_sec,
                skip_scanned=payload.skip_scanned,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/scan/status")
    def get_scan_status() -> dict[str, Any]:
        return scanner.get_status()

    @app.post("/api/scan/pause")
    def pause_scan() -> dict[str, Any]:
        return scanner.pause_scan()

    @app.post("/api/scan/resume")
    def resume_scan() -> dict[str, Any]:
        return scanner.resume_scan()

    @app.post("/api/scan/stop")
    def stop_scan() -> dict[str, Any]:
        return scanner.stop_scan()

    @app.post("/api/scan/reset")
    def reset_scan() -> dict[str, Any]:
        return scanner.reset_to_idle()

    # ------------------------------------------------------------------
    # History & Detections
    # ------------------------------------------------------------------
    @app.get("/api/history/summary")
    def get_summary() -> dict[str, Any]:
        return db.get_summary_stats()

    @app.get("/api/history/sessions")
    def get_sessions(limit: int = 100) -> list[dict[str, Any]]:
        return db.list_sessions(limit=limit)

    @app.get("/api/history/videos")
    def get_videos(
        session_id: int | None = None,
        only_with_person: bool = False,
        search: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return db.list_videos(
            session_id=session_id,
            only_with_person=only_with_person,
            search=search,
            limit=limit,
        )

    @app.get("/api/history/videos/{video_id}")
    def get_video_detail(video_id: int) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử video")
        detections = db.list_detections(video_id=video_id)
        return {"video": video, "detections": detections}

    @app.get("/api/history/detections")
    def get_detections(
        video_id: int | None = None,
        session_id: int | None = None,
        search: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return db.list_detections(
            video_id=video_id,
            session_id=session_id,
            search=search,
            limit=limit,
        )

    @app.post("/api/history/videos/{video_id}/relocate")
    def relocate_video(video_id: int, payload: RelocateVideoPayload) -> dict[str, Any]:
        new_file = Path(payload.new_path)
        if not new_file.is_file():
            raise HTTPException(status_code=400, detail="File video được chọn không tồn tại")
        updated = db.relocate_video(video_id, str(new_file))
        if not updated:
            raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi video")
        return updated

    @app.delete("/api/history/sessions/{session_id}")
    def remove_session(session_id: int) -> dict[str, bool]:
        db.delete_session(session_id, paths.snapshots_dir)
        return {"ok": True}

    @app.delete("/api/history/videos/{video_id}")
    def remove_video(video_id: int) -> dict[str, bool]:
        db.delete_video(video_id, paths.snapshots_dir)
        return {"ok": True}

    @app.delete("/api/history/all")
    def remove_all_history() -> dict[str, bool]:
        if scanner.is_running():
            raise HTTPException(status_code=400, detail="Không thể xóa lịch sử khi đang quét")
        db.clear_all_history(paths.snapshots_dir)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Video Playback & OS Helpers
    # ------------------------------------------------------------------
    @app.get("/api/video/{video_id}/prepare")
    def prepare_video(video_id: int) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Không tìm thấy video")
        vpath = Path(video["video_path"])
        if not vpath.is_file():
            return {
                "ready": False,
                "missing": True,
                "video": video,
            }
        try:
            will_transcode = needs_transcode(vpath)
            playable_path, transcoded = playback.get_playable_path(vpath)
            return {
                "ready": True,
                "missing": False,
                "transcoded": transcoded or will_transcode,
                "stream_url": f"/api/video/{video_id}/stream",
                "video": video,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/video/{video_id}/stream")
    def stream_video(video_id: int, request: Request):
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Không tìm thấy video")
        vpath = Path(video["video_path"])
        try:
            playable_path, _ = playback.get_playable_path(vpath)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return stream_video_range(playable_path, request)

    @app.post("/api/video/{video_id}/open-folder")
    def open_video_folder(video_id: int) -> dict[str, bool]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Không tìm thấy video")
        vpath = Path(video["video_path"])
        if vpath.is_file():
            subprocess.Popen(["explorer", "/select,", str(vpath)])
            return {"ok": True}
        folder = Path(video["folder_path"])
        if folder.is_dir():
            os.startfile(str(folder))
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Không tìm thấy file hoặc thư mục trên ổ đĩa")

    @app.post("/api/video/{video_id}/open-external")
    def open_video_external(video_id: int) -> dict[str, bool]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Không tìm thấy video")
        vpath = Path(video["video_path"])
        if not vpath.is_file():
            raise HTTPException(status_code=404, detail="Không tìm thấy file video trên ổ đĩa")
        os.startfile(str(vpath))
        return {"ok": True}

    @app.post("/api/app/open-data-dir")
    def open_data_dir() -> dict[str, bool]:
        os.startfile(str(paths.data_dir))
        return {"ok": True}

    return app
