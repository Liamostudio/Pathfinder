"""Camera lens intrinsics: checkerboard calibration and a small JSON store.

A camera's intrinsics (focal length, centre, lens distortion) only change if the lens, zoom or
capture resolution does, so they are calibrated once per camera/resolution and saved to
`calibration/<camera>.json`. Needs the `vision` extra; OpenCV is imported lazily.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

CALIBRATION_DIR = Path("calibration")


def _cv2():
    import cv2  # noqa: PLC0415
    return cv2


@dataclass
class Intrinsics:
    camera_matrix: np.ndarray  # 3x3
    dist: np.ndarray           # distortion coefficients
    size: tuple[int, int]      # (width, height) the calibration is valid for
    rms_px: float              # reprojection RMS on the calibration views

    def to_json(self) -> dict:
        return {"camera_matrix": self.camera_matrix.tolist(), "dist": self.dist.reshape(-1).tolist(),
                "size": list(self.size), "rms_px": self.rms_px}

    @classmethod
    def from_json(cls, data: dict) -> "Intrinsics":
        k = np.array(data["camera_matrix"], dtype=np.float64).reshape(3, 3)
        d = np.array(data["dist"], dtype=np.float64).reshape(-1)
        if not (np.isfinite(k).all() and np.isfinite(d).all()):
            raise ValueError("intrinsics must be finite")
        w, h = (int(v) for v in data["size"])
        return cls(k, d, (w, h), float(data["rms_px"]))

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "Intrinsics":
        return cls.from_json(json.loads(Path(path).read_text()))


def board_points(cols: int, rows: int, square_m: float) -> np.ndarray:
    """3-D inner-corner positions of a flat checkerboard (z = 0), row by row like OpenCV returns them."""
    if cols < 3 or rows < 3 or not math.isfinite(square_m) or square_m <= 0:
        raise ValueError("need at least 3x3 inner corners and a positive square size")
    grid = np.zeros((rows * cols, 3), np.float64)
    grid[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_m
    return grid


def find_board(frame, cols: int, rows: int):
    """Refined inner-corner pixels (N, 2) of the checkerboard in `frame`, or None."""
    cv2 = _cv2()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    ok, corners = cv2.findChessboardCornersSB(gray, (cols, rows), cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    return corners.reshape(-1, 2).astype(np.float64) if ok else None


def calibrate(views: list[np.ndarray], cols: int, rows: int, square_m: float,
              size: tuple[int, int], rational: bool = False) -> Intrinsics:
    """Intrinsics from several views of the board. `rational` adds distortion terms for very wide lenses."""
    cv2 = _cv2()
    if len(views) < 8:
        raise ValueError("need at least 8 board views for a stable calibration")
    obj = board_points(cols, rows, square_m)
    flags = cv2.CALIB_RATIONAL_MODEL if rational else 0
    rms, k, dist, _, _ = cv2.calibrateCamera(
        [obj.astype(np.float32)] * len(views), [v.astype(np.float32).reshape(-1, 1, 2) for v in views],
        size, None, None, flags=flags)
    return Intrinsics(k, dist.reshape(-1), (int(size[0]), int(size[1])), float(rms))
