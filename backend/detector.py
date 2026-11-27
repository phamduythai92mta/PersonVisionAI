"""ONNXRuntime YOLO11n person detector and snapshot renderer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class PersonBox:
    """Detected person bounding box in original image coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    nx1: float
    ny1: float
    nx2: float
    ny2: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "nx1": round(self.nx1, 4),
            "ny1": round(self.ny1, 4),
            "nx2": round(self.nx2, 4),
            "ny2": round(self.ny2, 4),
            "confidence": round(self.confidence, 4),
        }


def format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _load_ui_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a crisp Windows UI font supporting Vietnamese Unicode characters."""
    candidates = [
        Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "segoeui.ttf",
        Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "seguisb.ttf",
        Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf",
    ]
    for font_path in candidates:
        if font_path.is_file():
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


class PersonDetector:
    """Offline person detector powered by YOLO11n ONNX + ONNXRuntime."""

    def __init__(self, model_path: Path, input_size: int = 640) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"AI model file not found at: {model_path}")
        self.model_path = model_path
        self.input_size = input_size

        opts = ort.SessionOptions()
        cpu_threads = max(2, min(4, os.cpu_count() or 4))
        opts.intra_op_num_threads = cpu_threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def _preprocess(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        """Letterbox-resize BGR image to square input tensor [1, 3, S, S]."""
        h, w = frame_bgr.shape[:2]
        size = self.input_size
        scale = min(size / w, size / h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))

        if (new_w, new_h) != (w, h):
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=interp)
        else:
            resized = frame_bgr

        pad_x = (size - new_w) // 2
        pad_y = (size - new_h) // 2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

        blob = cv2.dnn.blobFromImage(
            canvas,
            scalefactor=1.0 / 255.0,
            size=(size, size),
            swapRB=True,
            crop=False,
        )
        return blob, scale, pad_x, pad_y

    def detect(
        self,
        frame_bgr: np.ndarray,
        conf_threshold: float = 0.45,
        iou_threshold: float = 0.45,
    ) -> list[PersonBox]:
        """Detect all persons (COCO class 0) in a BGR video frame."""
        if frame_bgr is None or frame_bgr.size == 0:
            return []

        orig_h, orig_w = frame_bgr.shape[:2]
        blob, scale, pad_x, pad_y = self._preprocess(frame_bgr)
        raw_out = self.session.run([self.output_name], {self.input_name: blob})[0]

        # Standard YOLO11/YOLOv8 export shape: (1, 84, 8400) -> (8400, 84)
        preds = np.squeeze(raw_out, axis=0)
        if preds.shape[0] < preds.shape[1]:
            preds = preds.T

        person_scores = preds[:, 4]
        other_max = np.max(preds[:, 5:], axis=1)
        # Keep anchors where person confidence meets threshold and isn't strongly beaten by another class
        mask = (person_scores >= conf_threshold) & (other_max <= person_scores + 0.20)
        candidates = preds[mask]
        if candidates.shape[0] == 0:
            return []

        scores = candidates[:, 4].astype(float).tolist()
        cx = candidates[:, 0]
        cy = candidates[:, 1]
        bw = candidates[:, 2]
        bh = candidates[:, 3]

        x1 = cx - bw * 0.5
        y1 = cy - bh * 0.5
        nms_boxes = np.stack([x1, y1, bw, bh], axis=1).tolist()

        indices = cv2.dnn.NMSBoxes(
            bboxes=nms_boxes,
            scores=scores,
            score_threshold=float(conf_threshold),
            nms_threshold=float(iou_threshold),
        )
        if len(indices) == 0:
            return []

        flat_indices = np.array(indices).reshape(-1).tolist()
        results: list[PersonBox] = []
        for idx in flat_indices:
            bx1 = (float(x1[idx]) - pad_x) / scale
            by1 = (float(y1[idx]) - pad_y) / scale
            bx2 = (float(x1[idx] + bw[idx]) - pad_x) / scale
            by2 = (float(y1[idx] + bh[idx]) - pad_y) / scale

            ix1 = max(0, min(orig_w - 1, int(round(bx1))))
            iy1 = max(0, min(orig_h - 1, int(round(by1))))
            ix2 = max(0, min(orig_w, int(round(bx2))))
            iy2 = max(0, min(orig_h, int(round(by2))))

            if (ix2 - ix1) < 8 or (iy2 - iy1) < 12:
                continue

            results.append(
                PersonBox(
                    x1=ix1,
                    y1=iy1,
                    x2=ix2,
                    y2=iy2,
                    confidence=float(scores[idx]),
                    nx1=ix1 / max(1, orig_w),
                    ny1=iy1 / max(1, orig_h),
                    nx2=ix2 / max(1, orig_w),
                    ny2=iy2 / max(1, orig_h),
                )
            )

        results.sort(key=lambda b: b.confidence, reverse=True)
        return results

    @staticmethod
    def save_detection_snapshots(
        frame_bgr: np.ndarray,
        boxes: list[PersonBox],
        timestamp_sec: float,
        full_snapshot_path: Path,
        crop_snapshot_path: Path | None = None,
    ) -> None:
        """Render annotated snapshot + close-up person crop using Unicode-safe disk writes."""
        full_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        h, w = frame_bgr.shape[:2]

        # 1. Save close-up crop of primary (highest-confidence) person before drawing boxes over it
        if crop_snapshot_path is not None and boxes:
            crop_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            primary = boxes[0]
            bw = primary.x2 - primary.x1
            bh = primary.y2 - primary.y1
            pad_w = int(bw * 0.18)
            pad_h = int(bh * 0.14)
            cx1 = max(0, primary.x1 - pad_w)
            cy1 = max(0, primary.y1 - pad_h)
            cx2 = min(w, primary.x2 + pad_w)
            cy2 = min(h, primary.y2 + pad_h)
            crop_bgr = frame_bgr[cy1:cy2, cx1:cx2]
            if crop_bgr.size > 0:
                ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    crop_snapshot_path.write_bytes(buf.tobytes())

        # 2. Render crisp annotated frame via Pillow (supports Vietnamese labels cleanly)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        stroke_w = max(2, min(5, int(round(min(w, h) / 260))))
        font_size = max(14, min(26, int(round(min(w, h) / 36))))
        font = _load_ui_font(font_size)

        for box in boxes:
            # Subtle tint inside box
            draw.rectangle(
                [box.x1, box.y1, box.x2, box.y2],
                fill=(16, 185, 129, 28),
                outline=(16, 185, 129, 245),
                width=stroke_w,
            )
            label = f"Person {int(round(box.confidence * 100))}%"
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            pad_px = 6
            pill_w = tw + pad_px * 2
            pill_h = th + pad_px * 2

            lx1 = max(0, min(w - pill_w, box.x1))
            ly1 = box.y1 - pill_h - 4
            if ly1 < 4:
                ly1 = min(h - pill_h - 4, box.y1 + 4)

            draw.rounded_rectangle(
                [lx1, ly1, lx1 + pill_w, ly1 + pill_h],
                radius=6,
                fill=(6, 78, 59, 225),
            )
            draw.text((lx1 + pad_px, ly1 + pad_px - 1), label, font=font, fill=(236, 253, 245, 255))

        # Timestamp & count badge at top-left
        noun = "person" if len(boxes) == 1 else "people"
        ts_label = f"{format_timestamp(timestamp_sec)}  ·  {len(boxes)} {noun}"
        tb = draw.textbbox((0, 0), ts_label, font=font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
        draw.rounded_rectangle(
            [12, 12, 12 + tw + 18, 12 + th + 14],
            radius=8,
            fill=(15, 23, 42, 210),
        )
        draw.text((21, 18), ts_label, font=font, fill=(248, 250, 252, 255))

        composed = Image.alpha_composite(img, overlay).convert("RGB")
        composed.save(str(full_snapshot_path), format="JPEG", quality=90, optimize=True)
