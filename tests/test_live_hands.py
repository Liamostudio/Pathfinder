"""Live hand-gate plumbing against a fake camera (rendered marker frames) and a fake detector. No hardware."""
import sys
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).parent))

from lookandpick.adapters.base import PixelHand  # noqa: E402
from lookandpick.adapters.fake import SimArm  # noqa: E402
from lookandpick.adapters.hands import FakeHandDetector  # noqa: E402
from lookandpick.adapters.live_camera import LatestFrameCamera  # noqa: E402
from lookandpick.live_hands import CalibrationError, calibrate_from_frames, run_live  # noqa: E402
from test_calibration import CAM_A, K, MARKER_M, render  # noqa: E402

FRAME = render(CAM_A)[0]


class FakeCapture:
    def __init__(self, period=0.0, frame=FRAME, fail_after=None, hang_after=None):
        self.period, self.frame, self.fail_after, self.hang_after = period, frame, fail_after, hang_after
        self.n, self.released = 0, False

    def isOpened(self):
        return True

    def read(self):
        self.n += 1
        if self.hang_after is not None and self.n > self.hang_after:
            time.sleep(5)
        time.sleep(self.period)
        if self.fail_after is not None and self.n > self.fail_after:
            return False, None
        return True, self.frame.copy()

    def release(self):
        self.released = True


def camera(**kw):
    stall = kw.pop("stall_s", 0.5)
    cap = FakeCapture(**kw)
    return LatestFrameCamera(0, "fake", stall_s=stall, open_capture=lambda: cap), cap


def test_slow_consumer_gets_fresh_frames_not_a_backlog():
    cam, cap = camera(period=0.002)
    ages, got = [], 0
    for stamp, _ in cam.frames():
        ages.append(time.monotonic() - stamp)
        got += 1
        time.sleep(0.05)
        if got >= 8:
            break
    cam.close()
    assert cam.frames_grabbed > got * 5 and max(ages) < 0.03 and cap.released


def test_camera_stall_raises():
    cam, _ = camera(hang_after=2, stall_s=0.2)
    with pytest.raises(RuntimeError, match="stalled"):
        for _ in cam.frames():
            pass
    cam.close()


def test_camera_failure_raises():
    cam, _ = camera(period=0.005, fail_after=3)
    with pytest.raises(RuntimeError, match="stopped delivering"):
        for _ in cam.frames():
            pass
    cam.close()


def frames_of(frame, n=40):
    return ((i / 30, frame) for i in range(n))


def test_calibration_from_frames_recovers_the_camera():
    cal = calibrate_from_frames(frames_of(FRAME), MARKER_M * 1000, K)
    assert np.linalg.norm(cal.camera_position() - np.array(CAM_A)) < 0.01


def test_calibration_fails_without_marker():
    with pytest.raises(CalibrationError, match="seen in only 0"):
        calibrate_from_frames(frames_of(np.full_like(FRAME, 128)), MARKER_M * 1000, K)


def test_poor_fit_is_rejected():
    bad_k = np.array([[300.0, 0, 640], [0, 300.0, 360], [0, 0, 1]])
    with pytest.raises(CalibrationError, match="poor"):
        calibrate_from_frames(frames_of(FRAME), MARKER_M * 1000, bad_k)


def hand_at(cal, xy, z=0.08):
    px = cal.table_to_pixel(xy, z)
    return [PixelHand("h", ((float(px[0]), float(px[1])),))]


def run(detections, cam=None, seconds=1.2):
    cal = calibrate_from_frames(frames_of(FRAME), MARKER_M * 1000, K)
    cam = cam or camera(period=0.02)[0]
    arm = SimArm(start=(0.0, 0.10, 0.05))
    return run_live(cam, FakeHandDetector(detections(cal)), cal, arm, seconds=seconds, hz=30), arm


def test_no_hand_means_full_speed():
    summary, arm = run(lambda cal: [])
    assert summary["frames"] > 10 and summary["min_scale"] == 1.0 and not summary["stopped"]
    assert any(kind == "move" for kind, _ in arm.log)


def test_hand_on_the_path_stops_the_arm():
    summary, arm = run(lambda cal: hand_at(cal, (0.0, 0.10)))
    assert summary["min_scale"] == 0.0 and summary["frames_with_hands"] > 10
    assert summary["decision_ms"]["max"] < 100


def test_hand_far_away_does_not_slow_it():
    summary, _ = run(lambda cal: hand_at(cal, (0.0, -0.5)))
    assert summary["min_scale"] == 1.0


def test_camera_stall_fails_safe_and_halts_the_arm():
    cam, _ = camera(period=0.02, hang_after=15, stall_s=0.15)
    summary, arm = run(lambda cal: [], cam=cam, seconds=2.0)
    assert summary["stopped"] and "stalled" in summary["final_reason"] and arm.halted
