"""Lens calibration recovers a known camera from synthetic checkerboard views (no hardware)."""
import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.intrinsics import Intrinsics, board_points, calibrate  # noqa: E402

SIZE = (1280, 720)
K = np.array([[610.0, 0, 645], [0, 612.0, 355], [0, 0, 1]])
DIST = np.array([-0.30, 0.09, 0.0005, -0.0004, -0.01])  # strong barrel, like a wide action cam


def synthetic_views(n=14, seed=1):
    rng = np.random.default_rng(seed)
    obj = board_points(6, 9, 0.02)
    centre = obj.mean(axis=0)
    views = []
    while len(views) < n:
        rvec = rng.uniform(-0.5, 0.5, 3)
        tvec = np.array([rng.uniform(-0.08, 0.08), rng.uniform(-0.05, 0.05), rng.uniform(0.30, 0.55)])
        pts, _ = cv2.projectPoints(obj - centre, rvec, tvec, K, DIST)
        pts = pts.reshape(-1, 2) + rng.normal(0, 0.15, (len(obj), 2))  # corner detection noise
        if (pts[:, 0] > 0).all() and (pts[:, 0] < SIZE[0]).all() and (pts[:, 1] > 0).all() and (pts[:, 1] < SIZE[1]).all():
            views.append(pts)
    return views


def test_known_camera_is_recovered():
    cal = calibrate(synthetic_views(), 6, 9, 0.02, SIZE)
    assert cal.rms_px < 0.5
    assert abs(cal.camera_matrix[0, 0] - K[0, 0]) / K[0, 0] < 0.02
    assert np.allclose(cal.camera_matrix[:2, 2], K[:2, 2], atol=8)
    assert abs(cal.dist[0] - DIST[0]) < 0.05


def test_too_few_views_rejected():
    with pytest.raises(ValueError, match="at least 8"):
        calibrate(synthetic_views(5), 6, 9, 0.02, SIZE)


def test_save_load_round_trip(tmp_path):
    cal = calibrate(synthetic_views(), 6, 9, 0.02, SIZE)
    cal.save(tmp_path / "cam.json")
    back = Intrinsics.load(tmp_path / "cam.json")
    assert np.allclose(back.camera_matrix, cal.camera_matrix) and np.allclose(back.dist, cal.dist)
    assert back.size == SIZE


def test_bad_board_rejected():
    with pytest.raises(ValueError):
        board_points(6, 9, 0)
