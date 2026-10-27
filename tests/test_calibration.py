"""Calibration against synthetic camera views rendered from known poses (no hardware)."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from lookandpick.calibration import TableCalibration, detect_marker  # noqa: E402

MARKER_M = 0.12
K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]])
SIZE = (1280, 720)


def look_at(pos, target=(0.0, 0.0, 0.0)):
    pos, target = np.asarray(pos, float), np.asarray(target, float)
    z = target - pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rot = np.vstack([x, y, z])
    rvec = cv2.Rodrigues(rot)[0].reshape(3)
    return rvec, -rot @ pos


def render(pos, marker_id=0):
    """A synthetic frame of the marker lying at the table origin, seen from `pos`."""
    rvec, tvec = look_at(pos)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side, pad = 600, 100
    marker = cv2.aruco.generateImageMarker(d, marker_id, side)
    src_img = cv2.copyMakeBorder(marker, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    src = np.float32([[pad, pad], [pad + side, pad], [pad + side, pad + side], [pad, pad + side]])
    h = MARKER_M / 2
    world = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], float)
    dst, _ = cv2.projectPoints(world, rvec, tvec, K, None)
    homography = cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32))
    frame = cv2.warpPerspective(src_img, homography, SIZE, flags=cv2.INTER_LINEAR, borderValue=128)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR), rvec, tvec


CAM_A = (0.0, -0.55, 0.45)   # in front of the marker
CAM_B = (0.55, 0.0, 0.45)    # ~90 degrees round the table


def calibrate(pos):
    frame, rvec, tvec = render(pos)
    corners = detect_marker(frame)
    assert corners is not None, "marker not found in synthetic frame"
    return TableCalibration.from_marker(corners, MARKER_M, K), rvec, tvec


def test_marker_pose_recovered():
    cal, rvec, tvec = calibrate(CAM_A)
    assert cal.reprojection_px < 1.0
    assert np.linalg.norm(cal.tvec - tvec) < 0.005          # 5 mm
    assert np.linalg.norm(cal.camera_position() - np.array(CAM_A)) < 0.01


def test_wrong_marker_id_not_found():
    frame, _, _ = render(CAM_A, marker_id=7)
    assert detect_marker(frame, marker_id=0) is None
    assert detect_marker(frame, marker_id=7) is not None


def test_pixel_to_table_roundtrip():
    cal, _, _ = calibrate(CAM_A)
    for xy in [(0.0, 0.0), (0.15, 0.10), (-0.20, 0.25), (0.30, -0.05)]:
        px = cal.table_to_pixel(xy)
        assert np.linalg.norm(cal.pixel_to_table(px) - np.array(xy)) < 0.005


def test_tapped_pixel_lands_on_right_spot_within_1cm():
    """Ground truth comes from the render camera, not the calibration being tested."""
    cal, rvec, tvec = calibrate(CAM_A)
    truth = np.array([0.18, 0.12, 0.0])
    px, _ = cv2.projectPoints(truth.reshape(1, 3), rvec, tvec, K, None)
    assert np.linalg.norm(cal.pixel_to_table(px.reshape(2)) - truth[:2]) < 0.01


def test_two_cameras_agree_on_a_table_point_within_1cm():
    cal_a, rvec_a, tvec_a = calibrate(CAM_A)
    cal_b, rvec_b, tvec_b = calibrate(CAM_B)
    truth = np.array([0.20, 0.10, 0.0])
    px_a, _ = cv2.projectPoints(truth.reshape(1, 3), rvec_a, tvec_a, K, None)
    px_b, _ = cv2.projectPoints(truth.reshape(1, 3), rvec_b, tvec_b, K, None)
    a = cal_a.pixel_to_table(px_a.reshape(2))
    b = cal_b.pixel_to_table(px_b.reshape(2))
    assert np.linalg.norm(a - b) < 0.01
    assert np.linalg.norm(a - truth[:2]) < 0.01


def test_object_height_is_respected():
    cal, rvec, tvec = calibrate(CAM_A)
    truth = np.array([0.10, 0.05, 0.08])  # top of a mug
    px, _ = cv2.projectPoints(truth.reshape(1, 3), rvec, tvec, K, None)
    assert np.linalg.norm(cal.pixel_to_table(px.reshape(2), z=0.08) - truth[:2]) < 0.01
    # Ignoring the height gives a visibly wrong table position - the reason `z` exists.
    assert np.linalg.norm(cal.pixel_to_table(px.reshape(2)) - truth[:2]) > 0.01


def test_ray_above_horizon_is_rejected():
    cal, _, _ = calibrate(CAM_A)
    with pytest.raises(ValueError):
        cal.pixel_to_table((640, -4000))
