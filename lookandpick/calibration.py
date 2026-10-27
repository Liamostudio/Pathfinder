"""Camera-to-table calibration from the ArUco marker at the arm base.

The marker lies flat on the table, so its plane is the table plane. World frame: origin at the
marker centre, x/y across the table, z up. Given the marker's four corners in an image we get the
camera pose (solvePnP), and any pixel can then be turned into a table position in metres by
intersecting its viewing ray with z = 0.

Needs the `vision` extra; OpenCV is imported lazily.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _cv2():
    import cv2  # noqa: PLC0415
    return cv2


def detect_marker(frame, marker_id: int = 0, dictionary: str = "DICT_4X4_50"):
    """Returns the marker's corners as a (4, 2) float array (TL, TR, BR, BL), or None."""
    cv2 = _cv2()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary)),
        cv2.aruco.DetectorParameters(),
    )
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return None
    for c, i in zip(corners, ids.flatten()):
        if int(i) == marker_id:
            return np.asarray(c, dtype=np.float64).reshape(4, 2)
    return None


def _marker_object_points(size_m: float) -> np.ndarray:
    h = size_m / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=np.float64)


@dataclass
class TableCalibration:
    """One camera's pose relative to the marker/table frame."""

    camera_matrix: np.ndarray  # 3x3 intrinsics
    rvec: np.ndarray           # world -> camera rotation (Rodrigues), shape (3,)
    tvec: np.ndarray           # world -> camera translation, shape (3,)
    dist: np.ndarray | None = None
    reprojection_px: float = 0.0  # RMS error of the marker corners; large means a bad fit

    @classmethod
    def from_marker(cls, corners, marker_size_m: float, camera_matrix, dist=None) -> "TableCalibration":
        cv2 = _cv2()
        obj = _marker_object_points(marker_size_m)
        img = np.asarray(corners, dtype=np.float64).reshape(4, 2)
        ok, rvec, tvec = cv2.solvePnP(obj, img, camera_matrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok:
            raise ValueError("solvePnP failed")
        proj, _ = cv2.projectPoints(obj, rvec, tvec, camera_matrix, dist)
        err = float(np.sqrt(np.mean(np.sum((proj.reshape(4, 2) - img) ** 2, axis=1))))
        return cls(np.asarray(camera_matrix, dtype=np.float64), rvec.reshape(3), tvec.reshape(3), dist, err)

    def _rotation(self) -> np.ndarray:
        return _cv2().Rodrigues(self.rvec)[0]

    def camera_position(self) -> np.ndarray:
        """Camera centre in the table frame (metres)."""
        return -self._rotation().T @ self.tvec

    def pixel_to_table(self, pixel, z: float = 0.0) -> np.ndarray:
        """Table-frame (x, y) in metres under `pixel`, for a point at height `z` above the table."""
        cv2 = _cv2()
        px = np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2)
        ray_cam = cv2.undistortPoints(px, self.camera_matrix, self.dist).reshape(2)
        d_cam = np.array([ray_cam[0], ray_cam[1], 1.0])
        rot = self._rotation()
        d_world = rot.T @ d_cam
        origin = self.camera_position()
        if abs(d_world[2]) < 1e-9:
            raise ValueError("pixel ray is parallel to the table")
        s = (z - origin[2]) / d_world[2]
        if s <= 0:
            raise ValueError("pixel ray points away from the table")
        return (origin + s * d_world)[:2]

    def table_to_pixel(self, xy, z: float = 0.0) -> np.ndarray:
        cv2 = _cv2()
        pt = np.array([[xy[0], xy[1], z]], dtype=np.float64)
        proj, _ = cv2.projectPoints(pt, self.rvec, self.tvec, self.camera_matrix, self.dist)
        return proj.reshape(2)
