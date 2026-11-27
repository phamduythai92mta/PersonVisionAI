"""Video playback preparation (browser-compatible MP4 transcoding cache & HTTP range streaming)."""

from __future__ import annotations

import hashlib
import mimetypes
import subprocess
import threading
from pathlib import Path
from typing import Iterator

import cv2
import imageio_ffmpeg
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


DIRECT_PLAY_EXTENSIONS = {".mp4", ".m4v", ".webm"}
WEB_FRIENDLY_FOURCCS = {
    "avc1",
    "h264",
    "H264",
    "X264",
    "x264",
    "vp80",
    "VP80",
    "vp90",
    "VP90",
    "av01",
    "AV01",
}


def _decode_fourcc(val: int) -> str:
    return "".join([chr((val >> (8 * i)) & 0xFF) for i in range(4)])


def _safe_cv2_path(path: Path) -> str:
    """Use Windows 8.3 short path fallback when OpenCV cannot open non-ASCII paths."""
    raw = str(path)
    if raw.isascii():
        return raw
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(512)
        res = ctypes.windll.kernel32.GetShortPathNameW(raw, buf, 512)
        if res > 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return raw


def needs_transcode(video_path: Path) -> bool:
    """Return True if the file container/codec is unlikely to play natively in Edge WebView2."""
    ext = video_path.suffix.lower()
    if ext not in DIRECT_PLAY_EXTENSIONS:
        return True
    if ext == ".webm":
        return False

    cap = cv2.VideoCapture(_safe_cv2_path(video_path))
    if not cap.isOpened():
        return True
    try:
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = _decode_fourcc(fourcc_int)
        return fourcc not in WEB_FRIENDLY_FOURCCS
    finally:
        cap.release()


class VideoPlaybackManager:
    """Manages browser-playable video sources and byte-range streaming."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get_playable_path(self, video_path: Path) -> tuple[Path, bool]:
        """
        Return (playable_path, was_transcoded).

        If the video is already H.264 MP4/WebM, returns the original path immediately.
        Otherwise transcodes to a cached H.264 MP4 using bundled ffmpeg.
        """
        if not video_path.is_file():
            raise FileNotFoundError(f"Video not found: {video_path}")

        if not needs_transcode(video_path):
            return video_path, False

        stat = video_path.stat()
        key_src = f"{video_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        cache_key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:16]
        cached_mp4 = self.cache_dir / f"preview_{cache_key}.mp4"

        if cached_mp4.is_file() and cached_mp4.stat().st_size > 0:
            return cached_mp4, True

        lock = self._get_lock(cache_key)
        with lock:
            if cached_mp4.is_file() and cached_mp4.stat().st_size > 0:
                return cached_mp4, True

            tmp_out = self.cache_dir / f"preview_{cache_key}.tmp.mp4"
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                str(video_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(tmp_out),
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                timeout=600,
            )
            if proc.returncode != 0 or not tmp_out.is_file():
                tmp_out.unlink(missing_ok=True)
                err_tail = proc.stderr.decode("utf-8", errors="ignore")[-400:]
                raise RuntimeError(f"Không thể chuyển đổi video để phát trong ứng dụng: {err_tail}")

            tmp_out.replace(cached_mp4)
            return cached_mp4, True


def stream_video_range(file_path: Path, request: Request) -> StreamingResponse:
    """Stream a video file with full HTTP 206 byte-range support for instant seeking."""
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file video")

    file_size = file_path.stat().st_size
    content_type, _ = mimetypes.guess_type(str(file_path))
    if not content_type:
        content_type = "video/mp4"

    range_header = request.headers.get("range")
    chunk_size = 1024 * 1024  # 1 MB chunks

    def iter_file(start: int, end: int) -> Iterator[bytes]:
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    if not range_header:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        }
        return StreamingResponse(
            iter_file(0, max(0, file_size - 1)),
            status_code=200,
            media_type=content_type,
            headers=headers,
        )

    try:
        units, rng = range_header.strip().split("=", 1)
        if units.lower() != "bytes":
            raise ValueError("Unsupported unit")
        start_str, end_str = rng.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Invalid Range header") from exc

    start = max(0, start)
    end = min(file_size - 1, end)
    if start > end or start >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }
    return StreamingResponse(
        iter_file(start, end),
        status_code=206,
        media_type=content_type,
        headers=headers,
    )
