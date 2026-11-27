"""SQLite persistence layer for scan sessions, scanned videos, detections, and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


DEFAULT_SETTINGS: dict[str, Any] = {
    "sample_interval_sec": 1.0,
    "confidence_threshold": 0.45,
    "merge_gap_sec": 3.0,
    "max_segment_sec": 15.0,
    "recursive": True,
    "skip_scanned": True,
}


def utc_now_iso() -> str:
    """Return local ISO-8601 timestamp string (seconds precision) for display & sorting."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    """Thread-safe SQLite database wrapper for video detection history."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_path TEXT NOT NULL,
                    recursive INTEGER NOT NULL DEFAULT 1,
                    sample_interval_sec REAL NOT NULL DEFAULT 1.0,
                    confidence_threshold REAL NOT NULL DEFAULT 0.45,
                    merge_gap_sec REAL NOT NULL DEFAULT 3.0,
                    status TEXT NOT NULL DEFAULT 'running',
                    total_videos INTEGER NOT NULL DEFAULT 0,
                    processed_videos INTEGER NOT NULL DEFAULT 0,
                    videos_with_person INTEGER NOT NULL DEFAULT 0,
                    total_detections INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS scanned_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
                    video_path TEXT NOT NULL,
                    video_name TEXT NOT NULL,
                    folder_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    duration_sec REAL NOT NULL DEFAULT 0.0,
                    fps REAL NOT NULL DEFAULT 0.0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    person_detected INTEGER NOT NULL DEFAULT 0,
                    detection_count INTEGER NOT NULL DEFAULT 0,
                    scanned_at TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES scanned_videos(id) ON DELETE CASCADE,
                    session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
                    timestamp_sec REAL NOT NULL,
                    start_sec REAL NOT NULL,
                    end_sec REAL NOT NULL,
                    max_confidence REAL NOT NULL,
                    person_count INTEGER NOT NULL DEFAULT 1,
                    snapshot_path TEXT NOT NULL,
                    crop_path TEXT,
                    boxes_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scanned_videos_session ON scanned_videos(session_id);
                CREATE INDEX IF NOT EXISTS idx_scanned_videos_path ON scanned_videos(video_path);
                CREATE INDEX IF NOT EXISTS idx_scanned_videos_name ON scanned_videos(video_name);
                CREATE INDEX IF NOT EXISTS idx_detections_video ON detections(video_id);
                CREATE INDEX IF NOT EXISTS idx_detections_session ON detections(session_id);
                """
            )
            # Safe migration for skipped_videos column on existing databases.
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(scan_sessions)").fetchall()
            }
            if "skipped_videos" not in cols:
                conn.execute(
                    "ALTER TABLE scan_sessions ADD COLUMN skipped_videos INTEGER NOT NULL DEFAULT 0"
                )

            # Recover any orphaned 'running' sessions left behind by an abrupt crash.
            conn.execute(
                """
                UPDATE scan_sessions
                SET status = 'cancelled', finished_at = COALESCE(finished_at, ?)
                WHERE status = 'running'
                """,
                (utc_now_iso(),),
            )
            for key, val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(val)),
                )
            # One-time migration so existing databases adopt skip_scanned=True by default.
            migrated = conn.execute(
                "SELECT value FROM app_settings WHERE key = '_skip_scanned_default_v2'"
            ).fetchone()
            if not migrated:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('skip_scanned', 'true') "
                    "ON CONFLICT(key) DO UPDATE SET value = 'true'"
                )
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('_skip_scanned_default_v2', 'true')"
                )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            for row in rows:
                if row["key"] not in DEFAULT_SETTINGS:
                    continue
                try:
                    settings[row["key"]] = json.loads(row["value"])
                except json.JSONDecodeError:
                    settings[row["key"]] = row["value"]
        return settings

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            for key, val in updates.items():
                if key in DEFAULT_SETTINGS:
                    conn.execute(
                        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(val)),
                    )
        return self.get_settings()

    # ------------------------------------------------------------------
    # Sessions & Videos
    # ------------------------------------------------------------------
    def create_session(
        self,
        folder_path: str,
        recursive: bool,
        sample_interval_sec: float,
        confidence_threshold: float,
        merge_gap_sec: float,
        total_videos: int,
        skipped_videos: int = 0,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scan_sessions (
                    folder_path, recursive, sample_interval_sec, confidence_threshold,
                    merge_gap_sec, status, total_videos, skipped_videos, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    folder_path,
                    1 if recursive else 0,
                    float(sample_interval_sec),
                    float(confidence_threshold),
                    float(merge_gap_sec),
                    int(total_videos),
                    int(skipped_videos),
                    utc_now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def create_video_record(
        self,
        session_id: int,
        video_path: str,
        video_name: str,
        folder_path: str,
        file_size: int,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scanned_videos (
                    session_id, video_path, video_name, folder_path, file_size,
                    status, scanned_at
                ) VALUES (?, ?, ?, ?, ?, 'scanning', ?)
                """,
                (session_id, video_path, video_name, folder_path, int(file_size), utc_now_iso()),
            )
            return int(cur.lastrowid)

    def update_video_metadata(
        self,
        video_id: int,
        duration_sec: float,
        fps: float,
        width: int,
        height: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scanned_videos
                SET duration_sec = ?, fps = ?, width = ?, height = ?
                WHERE id = ?
                """,
                (float(duration_sec), float(fps), int(width), int(height), int(video_id)),
            )

    def add_detection(
        self,
        video_id: int,
        session_id: int,
        timestamp_sec: float,
        start_sec: float,
        end_sec: float,
        max_confidence: float,
        person_count: int,
        snapshot_path: str,
        crop_path: str | None,
        boxes: list[dict[str, Any]],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO detections (
                    video_id, session_id, timestamp_sec, start_sec, end_sec,
                    max_confidence, person_count, snapshot_path, crop_path,
                    boxes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(video_id),
                    int(session_id),
                    float(timestamp_sec),
                    float(start_sec),
                    float(end_sec),
                    float(max_confidence),
                    int(person_count),
                    snapshot_path,
                    crop_path,
                    json.dumps(boxes),
                    utc_now_iso(),
                ),
            )
            conn.execute(
                """
                UPDATE scanned_videos
                SET person_detected = 1, detection_count = detection_count + 1
                WHERE id = ?
                """,
                (int(video_id),),
            )
            return int(cur.lastrowid)

    def finish_video_record(
        self,
        video_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scanned_videos
                SET status = ?, error_message = ?, scanned_at = ?
                WHERE id = ?
                """,
                (status, error_message, utc_now_iso(), int(video_id)),
            )

    def update_session_progress(
        self,
        session_id: int,
        processed_videos: int,
        videos_with_person: int,
        total_detections: int,
        status: str | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        with self._connect() as conn:
            if finished:
                conn.execute(
                    """
                    UPDATE scan_sessions
                    SET processed_videos = ?, videos_with_person = ?, total_detections = ?,
                        status = COALESCE(?, status), error_message = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(processed_videos),
                        int(videos_with_person),
                        int(total_detections),
                        status,
                        error_message,
                        utc_now_iso(),
                        int(session_id),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE scan_sessions
                    SET processed_videos = ?, videos_with_person = ?, total_detections = ?
                    WHERE id = ?
                    """,
                    (
                        int(processed_videos),
                        int(videos_with_person),
                        int(total_detections),
                        int(session_id),
                    ),
                )

    def get_already_scanned_paths(self) -> set[str]:
        """Return normalized lowercase paths of videos previously scanned successfully."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT video_path FROM scanned_videos WHERE status = 'done'"
            ).fetchall()
            return {str(Path(r["video_path"])).lower() for r in rows}

    def get_already_scanned_names(self) -> set[str]:
        """Return normalized lowercase filenames of videos previously scanned successfully."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT video_name FROM scanned_videos WHERE status = 'done'"
            ).fetchall()
            return {r["video_name"].strip().lower() for r in rows if r["video_name"]}

    # ------------------------------------------------------------------
    # History Queries
    # ------------------------------------------------------------------
    def get_summary_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            sessions_cnt = conn.execute("SELECT COUNT(*) FROM scan_sessions").fetchone()[0]
            videos_cnt = conn.execute(
                "SELECT COUNT(*) FROM scanned_videos WHERE status != 'scanning'"
            ).fetchone()[0]
            videos_with_person = conn.execute(
                "SELECT COUNT(*) FROM scanned_videos WHERE person_detected = 1"
            ).fetchone()[0]
            detections_cnt = conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
            return {
                "total_sessions": int(sessions_cnt),
                "total_videos": int(videos_cnt),
                "videos_with_person": int(videos_with_person),
                "total_detections": int(detections_cnt),
            }

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scan_sessions ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_videos(
        self,
        session_id: int | None = None,
        only_with_person: bool = False,
        search: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if session_id is not None:
            clauses.append("sv.session_id = ?")
            params.append(int(session_id))
        if only_with_person:
            clauses.append("sv.person_detected = 1")
        if search.strip():
            like = f"%{search.strip()}%"
            clauses.append("(sv.video_name LIKE ? OR sv.video_path LIKE ?)")
            params.extend([like, like])

        where_sql = " AND ".join(clauses)
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sv.*,
                       (
                           SELECT d.snapshot_path
                           FROM detections d
                           WHERE d.video_id = sv.id
                           ORDER BY d.max_confidence DESC, d.timestamp_sec ASC
                           LIMIT 1
                       ) AS preview_snapshot,
                       (
                           SELECT d.timestamp_sec
                           FROM detections d
                           WHERE d.video_id = sv.id
                           ORDER BY d.timestamp_sec ASC
                           LIMIT 1
                       ) AS first_detection_sec
                FROM scanned_videos sv
                WHERE {where_sql}
                ORDER BY sv.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["exists_on_disk"] = Path(item["video_path"]).is_file()
                results.append(item)
            return results

    def get_video(self, video_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scanned_videos WHERE id = ?",
                (int(video_id),),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["exists_on_disk"] = Path(item["video_path"]).is_file()
            return item

    def list_detections(
        self,
        video_id: int | None = None,
        session_id: int | None = None,
        search: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if video_id is not None:
            clauses.append("d.video_id = ?")
            params.append(int(video_id))
        if session_id is not None:
            clauses.append("d.session_id = ?")
            params.append(int(session_id))
        if search.strip():
            like = f"%{search.strip()}%"
            clauses.append("(sv.video_name LIKE ? OR sv.video_path LIKE ?)")
            params.extend([like, like])

        order = "d.timestamp_sec ASC" if video_id is not None else "d.id DESC"
        params.append(int(limit))
        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT d.*, sv.video_name, sv.video_path, sv.duration_sec, sv.folder_path
                FROM detections d
                JOIN scanned_videos sv ON sv.id = d.video_id
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT ?
                """,
                params,
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                try:
                    d["boxes"] = json.loads(d["boxes_json"]) if d["boxes_json"] else []
                except json.JSONDecodeError:
                    d["boxes"] = []
                d.pop("boxes_json", None)
                d["video_exists"] = Path(d["video_path"]).is_file()
                items.append(d)
            return items

    def relocate_video(self, video_id: int, new_path: str) -> dict[str, Any] | None:
        """Update a single video's path and optionally remap siblings from the same moved folder."""
        new_file = Path(new_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT video_path, folder_path FROM scanned_videos WHERE id = ?",
                (int(video_id),),
            ).fetchone()
            if not row:
                return None

            old_folder = Path(row["folder_path"])
            new_folder = new_file.parent

            conn.execute(
                """
                UPDATE scanned_videos
                SET video_path = ?, video_name = ?, folder_path = ?
                WHERE id = ?
                """,
                (str(new_file), new_file.name, str(new_folder), int(video_id)),
            )

            # Also helpfully remap sibling videos from the exact same old folder if their files exist in new_folder.
            if old_folder != new_folder:
                siblings = conn.execute(
                    "SELECT id, video_name, video_path FROM scanned_videos WHERE folder_path = ? AND id != ?",
                    (str(old_folder), int(video_id)),
                ).fetchall()
                for sib in siblings:
                    if not Path(sib["video_path"]).is_file():
                        candidate = new_folder / sib["video_name"]
                        if candidate.is_file():
                            conn.execute(
                                "UPDATE scanned_videos SET video_path = ?, folder_path = ? WHERE id = ?",
                                (str(candidate), str(new_folder), int(sib["id"])),
                            )

        return self.get_video(video_id)

    def delete_session(self, session_id: int, snapshots_dir: Path) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_path, crop_path FROM detections WHERE session_id = ?",
                (int(session_id),),
            ).fetchall()
            conn.execute("DELETE FROM scan_sessions WHERE id = ?", (int(session_id),))

        for r in rows:
            for col in ("snapshot_path", "crop_path"):
                rel = r[col]
                if rel:
                    (snapshots_dir / rel).unlink(missing_ok=True)

    def delete_video(self, video_id: int, snapshots_dir: Path) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_path, crop_path FROM detections WHERE video_id = ?",
                (int(video_id),),
            ).fetchall()
            conn.execute("DELETE FROM scanned_videos WHERE id = ?", (int(video_id),))

        for r in rows:
            for col in ("snapshot_path", "crop_path"):
                rel = r[col]
                if rel:
                    (snapshots_dir / rel).unlink(missing_ok=True)

    def clear_all_history(self, snapshots_dir: Path) -> None:
        with self._connect() as conn:
            rows = conn.execute("SELECT snapshot_path, crop_path FROM detections").fetchall()
            conn.execute("DELETE FROM detections")
            conn.execute("DELETE FROM scanned_videos")
            conn.execute("DELETE FROM scan_sessions")

        for r in rows:
            for col in ("snapshot_path", "crop_path"):
                rel = r[col]
                if rel:
                    (snapshots_dir / rel).unlink(missing_ok=True)
