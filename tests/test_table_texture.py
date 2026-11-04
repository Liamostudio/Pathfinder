"""The marker-plane table texture: a perspective photo of the marker and a checkerboard comes out square and metric."""
import cv2
import numpy as np
import pytest

from lookandpick.calibration import detect_marker
from lookandpick.table_texture import rectify

MARKER_M = 0.12


def synthetic_photo():
    """A table plane with the marker at the origin and a 15 mm checkerboard 0.4 m away, seen from an oblique camera (known homography)."""
    mm = 0.5
    plane = np.full((1000, 1600, 3), 140, np.uint8)                       # 0.5 mm per pixel: x -0.4..0.4 m, y -0.25..0.25 m
    def px(x, y):
        return int(round((x + 0.4) * 1000 / mm)), int(round((0.25 - y) * 1000 / mm))
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.copyMakeBorder(cv2.aruco.generateImageMarker(d, 0, 240), 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)      # 120 mm black + 40 px (20 mm) white
    ox, oy = px(-0.08, 0.08)
    plane[oy:oy + marker.shape[0], ox:ox + marker.shape[1]] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    s = 30                                                                  # 15 mm squares at 0.5 mm per pixel
    for r in range(7):
        for c in range(10):
            if (r + c) % 2 == 0:
                cx, cy = px(0.15, 0.05)
                plane[cy + r * s:cy + (r + 1) * s, cx + c * s:cx + (c + 1) * s] = 0
    src = np.float32([[0, 0], [1600, 0], [1600, 1000], [0, 1000]])
    dst = np.float32([[300, 200], [1500, 260], [1700, 900], [150, 800]])  # an oblique, keystoned view
    return cv2.warpPerspective(plane, cv2.getPerspectiveTransform(src, dst), (1800, 1100), borderValue=(20, 20, 20))


def test_the_marker_and_the_squares_come_out_square_and_metric():
    photo = synthetic_photo()
    corners = detect_marker(photo)
    assert corners is not None
    out = rectify(photo, corners, MARKER_M, (-0.4, 0.4, -0.25, 0.25), mm_per_px=0.5)
    assert out.shape[:2] == (1000, 1600)
    q = np.array(detect_marker(out), float).reshape(4, 2)
    sides = [np.linalg.norm(q[i] - q[(i + 1) % 4]) * 0.5 for i in range(4)]
    assert sides == pytest.approx([120.0] * 4, abs=0.6)                     # the black square is 120 mm on every side
    v1, v2 = q[1] - q[0], q[3] - q[0]
    assert abs(float(v1 @ v2)) / np.linalg.norm(v1) / np.linalg.norm(v2) < 0.01          # right angles
    ok, pts = cv2.findChessboardCornersSB(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY), (9, 6), cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE)
    assert ok
    grid = pts.reshape(6, 9, 2)
    pitch_x = np.linalg.norm(np.diff(grid, axis=1), axis=2).mean() * 0.5
    pitch_y = np.linalg.norm(np.diff(grid, axis=0), axis=2).mean() * 0.5
    assert pitch_x == pytest.approx(15.0, abs=0.4) and pitch_y == pytest.approx(15.0, abs=0.4)


def test_bad_arguments_are_refused():
    photo = np.zeros((100, 100, 3), np.uint8)
    corners = [[10, 10], [90, 10], [90, 90], [10, 90]]
    for extent in ((0.2, 0.1, -0.1, 0.1), (-0.1, 0.1, 0.1, -0.1)):
        with pytest.raises(ValueError):
            rectify(photo, corners, MARKER_M, extent)
    with pytest.raises(ValueError):
        rectify(photo, corners, MARKER_M, (-1, 1, -1, 1), mm_per_px=0.01)       # 200,000 px wide
    with pytest.raises(ValueError):
        rectify(photo, corners, 0.0, (-0.1, 0.1, -0.1, 0.1))
