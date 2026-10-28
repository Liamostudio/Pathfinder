"""Lightweight hand detector and table projection, with lazy optional imports."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from .base import PixelHand
from ..world import Hand


class TableProjector(Protocol):
    def pixel_to_table(self, pixel, z: float = 0.0): ...


def project_hands(detections: list[PixelHand], calibration: TableProjector, *,
                  hand_height: float, uncertainty_m: float = 0.03, table_to_arm=None,
                  unlocalisable_radius_m: float = 2.0) -> list[Hand]:
    """Enclose projected landmarks in a sphere plus a configured uncertainty margin.

    The height is an explicit assumption. MediaPipe's wrist-relative z is NOT table depth.
    This does not establish an adequate uncertainty bound for real hardware.

    A landmark whose viewing ray never meets the table plane at that height (a hand raised above it, or
    very near the camera) cannot be localised. It is then treated as a hand that could be anywhere: a large
    envelope (`unlocalisable_radius_m`) that stops the arm while it is visible, instead of raising and
    latching a permanent stop.
    """
    if not math.isfinite(hand_height) or hand_height < 0:
        raise ValueError('Hand height must be finite and nonnegative')
    if not math.isfinite(uncertainty_m) or uncertainty_m < 0:
        raise ValueError('Uncertainty must be finite and nonnegative')
    hands = []
    for detection in detections:
        if not detection.pixels:
            raise ValueError('A hand must have landmarks')
        positions, unlocalised = [], False
        for pixel in detection.pixels:
            if len(pixel) != 2 or not all(math.isfinite(v) for v in pixel):
                raise ValueError('Invalid hand landmark')
            try:
                xy = calibration.pixel_to_table(pixel, z=hand_height)
            except ValueError:
                unlocalised = True  # ray misses the plane at this height; handled below, not an error
                break
            if len(xy) != 2 or not all(math.isfinite(v) for v in xy):
                raise ValueError('Invalid projected hand position')
            point = (float(xy[0]), float(xy[1]), hand_height)
            if table_to_arm is not None:
                point = tuple(table_to_arm(point))
            if len(point) != 3 or not all(math.isfinite(v) for v in point):
                raise ValueError('Invalid table-to-arm transform')
            positions.append(point)
        if unlocalised:
            hands.append(Hand(detection.id, (0.0, 0.0, hand_height), unlocalisable_radius_m))
            continue
        centre = tuple(sum(p[i] for p in positions) / len(positions) for i in range(3))
        radius = max(math.dist(centre, p) for p in positions) + uncertainty_m
        hands.append(Hand(detection.id, centre, radius))
    return hands


class FakeHandDetector:
    def __init__(self, detections=None):
        self.detections = [] if detections is None else detections

    def detect(self, frame, timestamp_ms):
        return list(self.detections)

    def close(self):
        pass


class MediaPipeHandDetector:
    """CPU Tasks VIDEO mode: tracks between palm detections. One owner thread only."""

    def __init__(self, model_path: str | Path, *, max_hands: int = 2, confidence: float = 0.6):
        model_path = Path(model_path).expanduser()
        if not model_path.is_file():
            raise FileNotFoundError(f'Hand Landmarker model not found: {model_path}')
        if max_hands < 1 or not 0 <= confidence <= 1:
            raise ValueError('Invalid detector options')
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError('Install the hands extra: pip install -e ".[hands]"') from exc
        self._mp = mp
        self._max_hands = max_hands
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=max_hands,
            min_hand_detection_confidence=confidence, min_hand_presence_confidence=confidence,
            min_tracking_confidence=confidence,
        )
        self._detector = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self._last_timestamp = -1
        self._closed = False

    def detect(self, frame, timestamp_ms):
        import numpy as np
        if self._closed:
            raise RuntimeError('Detector is closed')
        if not isinstance(timestamp_ms, int) or timestamp_ms <= self._last_timestamp:
            raise ValueError('Detector timestamps must increase in integer milliseconds')
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) < 1:
            raise ValueError('Expected a nonempty uint8 BGR frame')
        height, width = frame.shape[:2]
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                               data=np.ascontiguousarray(frame[:, :, ::-1]))
        result = self._detector.detect_for_video(image, timestamp_ms)
        self._last_timestamp = timestamp_ms
        detections = []
        if len(result.hand_landmarks) > self._max_hands:
            raise RuntimeError('Unexpected hand count')
        for i, landmarks in enumerate(result.hand_landmarks):
            if len(landmarks) != 21:
                raise RuntimeError('Incomplete hand landmarks')
            pixels = tuple((float(p.x) * width, float(p.y) * height) for p in landmarks)
            if not all(math.isfinite(x) and math.isfinite(y) for x, y in pixels):
                raise RuntimeError('Non-finite detector result')
            detections.append(PixelHand(f'hand-{i}', pixels))
        return detections

    def close(self):
        if not self._closed:
            self._closed = True
            self._detector.close()
