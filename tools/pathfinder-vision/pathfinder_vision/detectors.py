"""Detectors, all MediaPipe running on the CPU. Each one says plainly what it is and what it finds."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

HAND_CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
                    (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17))
POSE_CONNECTIONS = ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
                    (23, 25), (25, 27), (24, 26), (26, 28), (0, 11), (0, 12))


@dataclass
class Detection:
    kind: str                      # "hand" or "person"
    label: str                     # "HAND 0", "PERSON 1"
    score: float | None
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in image pixels
    points: list[tuple[float, float]] = field(default_factory=list)
    connections: tuple = ()


def _box(points, width, height, pad=0.0):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    px, py = pad * (max(xs) - min(xs)), pad * (max(ys) - min(ys))
    return (max(0, int(min(xs) - px)), max(0, int(min(ys) - py)),
            min(width - 1, int(max(xs) + px)), min(height - 1, int(max(ys) + py)))


class _MediaPipe:
    """Shared plumbing: lazy import, strictly increasing millisecond timestamps."""

    def __init__(self, model: Path) -> None:
        self.model = Path(model)
        if not self.model.is_file():
            raise FileNotFoundError(f"model file not found: {self.model}")
        try:
            import mediapipe as mp  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("mediapipe is not installed; run install.sh") from exc
        self._mp, self._last_ts = mp, -1

    def _image(self, frame):
        import numpy as np  # noqa: PLC0415
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame[:, :, ::-1]))

    def _ts(self, timestamp_ms: int) -> int:
        self._last_ts = max(self._last_ts + 1, int(timestamp_ms))
        return self._last_ts

    def close(self):
        self._detector.close()


class HandDetector(_MediaPipe):
    key, title = "hands", "HANDS"
    what = "MediaPipe Hand Landmarker"
    backend = "CPU"

    def __init__(self, model: Path, max_hands: int = 2, confidence: float = 0.6) -> None:
        super().__init__(model)
        mp = self._mp
        self._detector = mp.tasks.vision.HandLandmarker.create_from_options(mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=max_hands,
            min_hand_detection_confidence=confidence, min_hand_presence_confidence=confidence,
            min_tracking_confidence=confidence))

    def detect(self, frame, timestamp_ms: int) -> list[Detection]:
        h, w = frame.shape[:2]
        result = self._detector.detect_for_video(self._image(frame), self._ts(timestamp_ms))
        out = []
        for i, landmarks in enumerate(result.hand_landmarks):
            pts = [(p.x * w, p.y * h) for p in landmarks]
            score = result.handedness[i][0].score if result.handedness and result.handedness[i] else None
            out.append(Detection("hand", f"HAND {i}", score, _box(pts, w, h, 0.15), pts, HAND_CONNECTIONS))
        return out


class PersonBoxDetector(_MediaPipe):
    key, title = "person-box", "PEOPLE (boxes)"
    what = "MediaPipe EfficientDet-Lite0, 'person' class only"
    backend = "CPU"

    def __init__(self, model: Path, confidence: float = 0.4, max_people: int = 4) -> None:
        super().__init__(model)
        mp = self._mp
        self._detector = mp.tasks.vision.ObjectDetector.create_from_options(mp.tasks.vision.ObjectDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, score_threshold=confidence,
            max_results=max_people, category_allowlist=["person"]))

    def detect(self, frame, timestamp_ms: int) -> list[Detection]:
        h, w = frame.shape[:2]
        result = self._detector.detect_for_video(self._image(frame), self._ts(timestamp_ms))
        out = []
        for i, d in enumerate(result.detections):
            b = d.bounding_box
            score = d.categories[0].score if d.categories else None
            out.append(Detection("person", f"PERSON {i}", score,
                                 (max(0, b.origin_x), max(0, b.origin_y), min(w - 1, b.origin_x + b.width),
                                  min(h - 1, b.origin_y + b.height))))
        return out


class PersonSkeletonDetector(_MediaPipe):
    key, title = "person-skeleton", "PEOPLE (skeletons)"
    what = "MediaPipe Pose Landmarker lite, 33 body points"
    backend = "CPU"

    def __init__(self, model: Path, confidence: float = 0.5, max_people: int = 3) -> None:
        super().__init__(model)
        mp = self._mp
        self._detector = mp.tasks.vision.PoseLandmarker.create_from_options(mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_poses=max_people,
            min_pose_detection_confidence=confidence, min_pose_presence_confidence=confidence,
            min_tracking_confidence=confidence))

    def detect(self, frame, timestamp_ms: int) -> list[Detection]:
        h, w = frame.shape[:2]
        result = self._detector.detect_for_video(self._image(frame), self._ts(timestamp_ms))
        out = []
        for i, landmarks in enumerate(result.pose_landmarks):
            pts = [(p.x * w, p.y * h) for p in landmarks]
            seen = [pt for pt, p in zip(pts, landmarks) if (p.visibility or 0) > 0.3 and math.isfinite(pt[0] + pt[1])]
            if len(seen) < 4:
                continue
            out.append(Detection("person", f"PERSON {i}", None, _box(seen, w, h, 0.05), pts, POSE_CONNECTIONS))
        return out
