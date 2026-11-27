"""Background video folder scanning engine with smart event grouping and live progress."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .database import Database
from .detector import PersonBox, PersonDetector, format_timestamp
from .paths import AppPaths
from .video_service import _safe_cv2_path

VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".mpg",
    ".mpeg",
    ".3gp",
}


def discover_videos(folder: Path, recursive: bool) -> list[Path]:
    """Find supported video files inside folder in deterministic sorted order."""
    if not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = [
        p
        for p in iterator
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda p: str(p).lower())
    return files


@dataclass
class _ActiveEvent:
    start_sec: float
    end_sec: float
    last_seen_sec: float
    best_timestamp_sec: float
    best_confidence: float
    max_person_count: int
    best_frame: np.ndarray
    best_boxes: list[PersonBox] = field(default_factory=list)


class ScannerManager:
    """Coordinates background folder scanning, live status updates, pause, and cancellation."""

    def __init__(self, paths: AppPaths, db: Database) -> None:
        self.paths = paths
        self.db = db
        self._detector: PersonDetector | None = None
        self._detector_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = running, clear = paused

        self._status: dict[str, Any] = self._idle_state()

    def _idle_state(self) -> dict[str, Any]:
        return {
            "active": False,
            "paused": False,
            "status": "idle",
            "session_id": None,
            "folder_path": "",
            "total_found": 0,
            "skipped_videos": 0,
            "skip_notice": "",
            "total_videos": 0,
            "processed_videos": 0,
            "current_video_index": 0,
            "current_video_name": "",
            "current_video_path": "",
            "video_progress_pct": 0.0,
            "overall_progress_pct": 0.0,
            "videos_with_person": 0,
            "total_detections": 0,
            "elapsed_sec": 0.0,
            "recent_detections": [],
            "error_message": None,
        }

    def get_detector(self) -> PersonDetector:
        """Lazily load and cache the ONNXRuntime person detector."""
        with self._detector_lock:
            if self._detector is None:
                self._detector = PersonDetector(self.paths.model_path)
            return self._detector

    def get_status(self) -> dict[str, Any]:
        with self._state_lock:
            snap = dict(self._status)
            snap["recent_detections"] = list(self._status["recent_detections"])
            return snap

    def reset_to_idle(self) -> dict[str, Any]:
        """Reset scan status back to idle if no scan is currently active."""
        with self._state_lock:
            if not self._status["active"]:
                self._status = self._idle_state()
            snap = dict(self._status)
            snap["recent_detections"] = list(self._status["recent_detections"])
            return snap

    def is_running(self) -> bool:
        with self._state_lock:
            return bool(self._status["active"])

    def start_scan(
        self,
        folder_path: str,
        recursive: bool = True,
        sample_interval_sec: float = 1.0,
        confidence_threshold: float = 0.45,
        merge_gap_sec: float = 3.0,
        max_segment_sec: float = 15.0,
        skip_scanned: bool = True,
    ) -> dict[str, Any]:
        folder = Path(folder_path).expanduser()
        if not folder.is_dir():
            raise ValueError(f"Thư mục không tồn tại: {folder_path}")

        with self._state_lock:
            if self._status["active"]:
                raise RuntimeError("Đang có một tác vụ quét đang chạy.")

        all_videos = discover_videos(folder, recursive=recursive)
        if not all_videos:
            raise ValueError("Không tìm thấy file video hợp lệ nào trong thư mục đã chọn.")

        already_names = self.db.get_already_scanned_names()
        matched_scanned = [v for v in all_videos if v.name.strip().lower() in already_names]

        if skip_scanned:
            videos = [v for v in all_videos if v.name.strip().lower() not in already_names]
            skipped_count = len(matched_scanned)
        else:
            videos = all_videos
            skipped_count = 0

        if not videos:
            notice = (
                f"Đã có {skipped_count}/{len(all_videos)} video được quét trước đó "
                f"(trùng tên trong lịch sử). Không có video mới nào cần quét lại."
            )
            with self._state_lock:
                self._status = {
                    **self._idle_state(),
                    "status": "all_skipped",
                    "folder_path": str(folder),
                    "total_found": len(all_videos),
                    "skipped_videos": skipped_count,
                    "skip_notice": notice,
                }
            return self.get_status()

        skip_notice = ""
        if skipped_count > 0:
            skip_notice = (
                f"Đã bỏ qua {skipped_count}/{len(all_videos)} video đã được quét trước đó "
                f"(trùng tên trong lịch sử). Đang quét {len(videos)} video mới."
            )

        sample_interval_sec = max(0.2, min(30.0, float(sample_interval_sec)))
        confidence_threshold = max(0.15, min(0.95, float(confidence_threshold)))
        merge_gap_sec = max(0.0, min(60.0, float(merge_gap_sec)))
        max_segment_sec = max(2.0, min(120.0, float(max_segment_sec)))

        session_id = self.db.create_session(
            folder_path=str(folder),
            recursive=recursive,
            sample_interval_sec=sample_interval_sec,
            confidence_threshold=confidence_threshold,
            merge_gap_sec=merge_gap_sec,
            total_videos=len(videos),
            skipped_videos=skipped_count,
        )

        self._stop_event.clear()
        self._pause_event.set()

        with self._state_lock:
            self._status = {
                "active": True,
                "paused": False,
                "status": "running",
                "session_id": session_id,
                "folder_path": str(folder),
                "total_found": len(all_videos),
                "skipped_videos": skipped_count,
                "skip_notice": skip_notice,
                "total_videos": len(videos),
                "processed_videos": 0,
                "current_video_index": 0,
                "current_video_name": "",
                "current_video_path": "",
                "video_progress_pct": 0.0,
                "overall_progress_pct": 0.0,
                "videos_with_person": 0,
                "total_detections": 0,
                "elapsed_sec": 0.0,
                "recent_detections": [],
                "error_message": None,
            }

        self._worker = threading.Thread(
            target=self._run_scan_loop,
            args=(
                session_id,
                videos,
                sample_interval_sec,
                confidence_threshold,
                merge_gap_sec,
                max_segment_sec,
            ),
            daemon=True,
        )
        self._worker.start()
        return self.get_status()

    def pause_scan(self) -> dict[str, Any]:
        with self._state_lock:
            if self._status["active"]:
                self._pause_event.clear()
                self._status["paused"] = True
                self._status["status"] = "paused"
        return self.get_status()

    def resume_scan(self) -> dict[str, Any]:
        with self._state_lock:
            if self._status["active"]:
                self._pause_event.set()
                self._status["paused"] = False
                self._status["status"] = "running"
        return self.get_status()

    def stop_scan(self) -> dict[str, Any]:
        self._stop_event.set()
        self._pause_event.set()
        with self._state_lock:
            if self._status["active"]:
                self._status["status"] = "stopping"
                self._status["paused"] = False
        return self.get_status()

    def _update_state(self, **kwargs: Any) -> None:
        with self._state_lock:
            self._status.update(kwargs)

    def _append_recent_detection(self, item: dict[str, Any]) -> None:
        with self._state_lock:
            recent = self._status["recent_detections"]
            recent.insert(0, item)
            if len(recent) > 12:
                del recent[12:]

    def _flush_event(
        self,
        session_id: int,
        video_id: int,
        video_name: str,
        event: _ActiveEvent,
        det_counter: int,
    ) -> dict[str, Any]:
        ts_ms = int(round(event.best_timestamp_sec * 1000))
        rel_dir = f"session_{session_id}"
        snap_rel = f"{rel_dir}/vid_{video_id}_{det_counter:04d}_{ts_ms}ms.jpg"
        crop_rel = f"{rel_dir}/vid_{video_id}_{det_counter:04d}_{ts_ms}ms_crop.jpg"

        PersonDetector.save_detection_snapshots(
            frame_bgr=event.best_frame,
            boxes=event.best_boxes,
            timestamp_sec=event.best_timestamp_sec,
            full_snapshot_path=self.paths.snapshots_dir / snap_rel,
            crop_snapshot_path=self.paths.snapshots_dir / crop_rel,
        )

        det_id = self.db.add_detection(
            video_id=video_id,
            session_id=session_id,
            timestamp_sec=event.best_timestamp_sec,
            start_sec=event.start_sec,
            end_sec=event.end_sec,
            max_confidence=event.best_confidence,
            person_count=event.max_person_count,
            snapshot_path=snap_rel,
            crop_path=crop_rel if (self.paths.snapshots_dir / crop_rel).is_file() else None,
            boxes=[b.to_dict() for b in event.best_boxes],
        )

        return {
            "id": det_id,
            "video_id": video_id,
            "video_name": video_name,
            "timestamp_sec": round(event.best_timestamp_sec, 2),
            "start_sec": round(event.start_sec, 2),
            "end_sec": round(event.end_sec, 2),
            "formatted_time": format_timestamp(event.best_timestamp_sec),
            "max_confidence": round(event.best_confidence, 3),
            "person_count": event.max_person_count,
            "snapshot_path": snap_rel,
            "crop_path": crop_rel if (self.paths.snapshots_dir / crop_rel).is_file() else None,
        }

    def _run_scan_loop(
        self,
        session_id: int,
        videos: list[Path],
        sample_interval_sec: float,
        confidence_threshold: float,
        merge_gap_sec: float,
        max_segment_sec: float,
    ) -> None:
        t_start = time.monotonic()
        processed_videos = 0
        videos_with_person = 0
        total_detections = 0
        final_status = "completed"
        session_error: str | None = None

        try:
            detector = self.get_detector()
            total_videos = len(videos)

            for idx, video_path in enumerate(videos):
                if self._stop_event.is_set():
                    final_status = "cancelled"
                    break

                self._update_state(
                    current_video_index=idx + 1,
                    current_video_name=video_path.name,
                    current_video_path=str(video_path),
                    video_progress_pct=0.0,
                    overall_progress_pct=round((idx / total_videos) * 100.0, 1),
                    elapsed_sec=round(time.monotonic() - t_start, 1),
                )

                file_size = video_path.stat().st_size if video_path.is_file() else 0
                video_id = self.db.create_video_record(
                    session_id=session_id,
                    video_path=str(video_path),
                    video_name=video_path.name,
                    folder_path=str(video_path.parent),
                    file_size=file_size,
                )

                video_det_count = 0
                try:
                    video_det_count = self._scan_single_video(
                        detector=detector,
                        session_id=session_id,
                        video_id=video_id,
                        video_path=video_path,
                        video_index=idx,
                        total_videos=total_videos,
                        t_start=t_start,
                        sample_interval_sec=sample_interval_sec,
                        confidence_threshold=confidence_threshold,
                        merge_gap_sec=merge_gap_sec,
                        max_segment_sec=max_segment_sec,
                    )
                    if self._stop_event.is_set():
                        self.db.finish_video_record(video_id, "cancelled")
                        final_status = "cancelled"
                    else:
                        self.db.finish_video_record(video_id, "done")
                except Exception as exc:
                    self.db.finish_video_record(video_id, "error", str(exc))

                processed_videos += 1
                if video_det_count > 0:
                    videos_with_person += 1
                total_detections += video_det_count

                self.db.update_session_progress(
                    session_id=session_id,
                    processed_videos=processed_videos,
                    videos_with_person=videos_with_person,
                    total_detections=total_detections,
                )
                self._update_state(
                    processed_videos=processed_videos,
                    videos_with_person=videos_with_person,
                    total_detections=total_detections,
                    overall_progress_pct=round((processed_videos / total_videos) * 100.0, 1),
                    elapsed_sec=round(time.monotonic() - t_start, 1),
                )

                if self._stop_event.is_set():
                    final_status = "cancelled"
                    break

        except Exception as exc:
            final_status = "error"
            session_error = str(exc)

        self.db.update_session_progress(
            session_id=session_id,
            processed_videos=processed_videos,
            videos_with_person=videos_with_person,
            total_detections=total_detections,
            status=final_status,
            error_message=session_error,
            finished=True,
        )
        with self._state_lock:
            vid_pct = 100.0 if final_status == "completed" else self._status["video_progress_pct"]
            overall_pct = 100.0 if final_status == "completed" else self._status["overall_progress_pct"]
            skipped_cnt = self._status.get("skipped_videos", 0)
            total_found = self._status.get("total_found", 0)
            finish_notice = ""
            if skipped_cnt > 0:
                finish_notice = (
                    f"Đã bỏ qua {skipped_cnt}/{total_found} video đã được quét trước đó "
                    f"(trùng tên trong lịch sử). Đã quét {processed_videos} video mới."
                )
            self._status.update(
                active=False,
                paused=False,
                status=final_status,
                video_progress_pct=vid_pct,
                overall_progress_pct=overall_pct,
                elapsed_sec=round(time.monotonic() - t_start, 1),
                error_message=session_error,
                skip_notice=finish_notice or self._status.get("skip_notice", ""),
            )

    def _scan_single_video(
        self,
        detector: PersonDetector,
        session_id: int,
        video_id: int,
        video_path: Path,
        video_index: int,
        total_videos: int,
        t_start: float,
        sample_interval_sec: float,
        confidence_threshold: float,
        merge_gap_sec: float,
        max_segment_sec: float,
    ) -> int:
        cap = cv2.VideoCapture(_safe_cv2_path(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Không thể mở file video: {video_path.name}")

        det_count = 0
        active_event: _ActiveEvent | None = None

        try:
            raw_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            fps = raw_fps if 1.0 <= raw_fps <= 240.0 else 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            duration_sec = (total_frames / fps) if total_frames > 0 else 0.0

            self.db.update_video_metadata(video_id, duration_sec, fps, width, height)

            step_frames = max(1, int(round(fps * sample_interval_sec)))
            use_seek = total_frames > 0 and step_frames > 120

            frame_idx = 0
            while True:
                if self._stop_event.is_set():
                    break
                while not self._pause_event.is_set():
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.15)
                if self._stop_event.is_set():
                    break

                if use_seek:
                    if frame_idx >= total_frames:
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                    cur_sec = (pos_msec / 1000.0) if pos_msec > 0 else (frame_idx / fps)
                    next_target = frame_idx + step_frames
                else:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                    cur_sec = (pos_msec / 1000.0) if pos_msec > 0 else (frame_idx / fps)

                # Run AI person inference
                boxes = detector.detect(frame, conf_threshold=confidence_threshold)

                if boxes:
                    top_conf = boxes[0].confidence
                    person_cnt = len(boxes)
                    if active_event is None:
                        active_event = _ActiveEvent(
                            start_sec=cur_sec,
                            end_sec=cur_sec,
                            last_seen_sec=cur_sec,
                            best_timestamp_sec=cur_sec,
                            best_confidence=top_conf,
                            max_person_count=person_cnt,
                            best_frame=frame.copy(),
                            best_boxes=boxes,
                        )
                    else:
                        gap = cur_sec - active_event.last_seen_sec
                        seg_len = cur_sec - active_event.start_sec
                        if merge_gap_sec <= 0.0 or gap > merge_gap_sec or seg_len >= max_segment_sec:
                            det_count += 1
                            item = self._flush_event(session_id, video_id, video_path.name, active_event, det_count)
                            self._append_recent_detection(item)
                            with self._state_lock:
                                self._status["total_detections"] += 1
                            active_event = _ActiveEvent(
                                start_sec=cur_sec,
                                end_sec=cur_sec,
                                last_seen_sec=cur_sec,
                                best_timestamp_sec=cur_sec,
                                best_confidence=top_conf,
                                max_person_count=person_cnt,
                                best_frame=frame.copy(),
                                best_boxes=boxes,
                            )
                        else:
                            active_event.end_sec = cur_sec
                            active_event.last_seen_sec = cur_sec
                            if person_cnt > active_event.max_person_count:
                                active_event.max_person_count = person_cnt
                            if (top_conf > active_event.best_confidence) or (
                                abs(top_conf - active_event.best_confidence) < 0.04
                                and person_cnt > len(active_event.best_boxes)
                            ):
                                active_event.best_confidence = top_conf
                                active_event.best_timestamp_sec = cur_sec
                                active_event.best_frame = frame.copy()
                                active_event.best_boxes = boxes
                elif active_event is not None:
                    if (cur_sec - active_event.last_seen_sec) > merge_gap_sec:
                        det_count += 1
                        item = self._flush_event(session_id, video_id, video_path.name, active_event, det_count)
                        self._append_recent_detection(item)
                        with self._state_lock:
                            self._status["total_detections"] += 1
                        active_event = None

                # Update progress
                if total_frames > 0:
                    vid_pct = min(99.5, (frame_idx / total_frames) * 100.0)
                else:
                    vid_pct = 0.0
                overall_pct = ((video_index + (vid_pct / 100.0)) / total_videos) * 100.0
                self._update_state(
                    video_progress_pct=round(vid_pct, 1),
                    overall_progress_pct=round(min(99.9, overall_pct), 1),
                    elapsed_sec=round(time.monotonic() - t_start, 1),
                )

                if use_seek:
                    frame_idx = next_target
                else:
                    # Skip step_frames - 1 frames using lightweight grab()
                    skipped = 0
                    while skipped < (step_frames - 1):
                        if self._stop_event.is_set():
                            break
                        if not cap.grab():
                            skipped = -1
                            break
                        skipped += 1
                        frame_idx += 1
                    if skipped < 0:
                        break
                    frame_idx += 1

            # Flush trailing active event
            if active_event is not None:
                det_count += 1
                item = self._flush_event(session_id, video_id, video_path.name, active_event, det_count)
                self._append_recent_detection(item)
                with self._state_lock:
                    self._status["total_detections"] += 1

            # Update actual duration if metadata total_frames was missing
            if duration_sec <= 0 and frame_idx > 0:
                self.db.update_video_metadata(video_id, frame_idx / fps, fps, width, height)

        finally:
            cap.release()

        return det_count
