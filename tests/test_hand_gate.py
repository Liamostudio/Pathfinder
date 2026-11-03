"""HandGate: the live hand loop behind the runtime. Real watchdog and threads; fake camera, detector and arm."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.adapters.base import PixelHand  # noqa: E402
from lookandpick.adapters.fake import FakeTracker  # noqa: E402
from lookandpick.adapters.hands import FakeHandDetector  # noqa: E402
from lookandpick.calibration import TableCalibration, detect_marker  # noqa: E402
from lookandpick.hand_gate import NO_GATE_HAND, GateTracker, HandGate, TargetTrackingArm  # noqa: E402
from lookandpick.models.base import ChatResult, FakeLLM, ToolCall  # noqa: E402
from lookandpick.models.calllog import CallLog  # noqa: E402
from lookandpick.runtime import Runtime  # noqa: E402
from lookandpick.safety import Safety  # noqa: E402
from lookandpick.status_server import fake_gate_status, make_handler  # noqa: E402
from lookandpick.world import TrackedObject  # noqa: E402

MARKER_M, K = 0.12, np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]])
HAND_HEIGHT = 0.08


def calibration():
    pos = np.array([0.0, -0.55, 0.45])
    z = -pos / np.linalg.norm(pos)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    rot = np.vstack([x, np.cross(z, x), z])
    rvec, tvec = cv2.Rodrigues(rot)[0].reshape(3), -rot @ pos
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side, pad = 600, 100
    img = cv2.copyMakeBorder(cv2.aruco.generateImageMarker(d, 0, side), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    src = np.float32([[pad, pad], [pad + side, pad], [pad + side, pad + side], [pad, pad + side]])
    h = MARKER_M / 2
    dst, _ = cv2.projectPoints(np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], float), rvec, tvec, K, None)
    frame = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32)), (1280, 720), borderValue=128)
    return TableCalibration.from_marker(detect_marker(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)), MARKER_M, K)


CAL = calibration()


def hand_at(x, y):
    pixel = tuple(float(v) for v in CAL.table_to_pixel((x, y), HAND_HEIGHT))
    return PixelHand("hand-0", (pixel,) * 21)


class FakeCamera:
    """Delivers a fresh frame every 20 ms until `frozen` is set (a stalled camera) or it is closed."""

    def __init__(self):
        self.frozen, self._stop, self._seq, self.started = False, threading.Event(), 0, False

    def start(self):
        self.started = True

    def latest(self):
        return (time.monotonic(), np.zeros((10, 10, 3), np.uint8), self._seq)

    def frames(self):
        while not self._stop.is_set():
            if not self.frozen:
                self._seq += 1
                yield time.monotonic(), np.zeros((10, 10, 3), np.uint8)
            if self._stop.wait(0.02):
                return

    def close(self):
        self._stop.set()


class FakeArm:
    def __init__(self):
        self.position, self.scales, self.halted = (0.0, 0.10, 0.05), [], False

    def move_to(self, target, speed_scale):
        self.position = tuple(target)
        return True

    def grip(self, closed):
        return True

    def halt(self):
        self.halted = True

    def set_speed_scale(self, scale):
        self.scales.append(scale)


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def gate():
    detector, camera, arm = FakeHandDetector([]), FakeCamera(), FakeArm()
    g = HandGate(camera, lambda: detector, CAL, arm, hand_height=HAND_HEIGHT, warmup_s=2.0)
    g.detector = detector
    yield g
    g.close()


def test_the_gate_starts_clear_with_no_hands_and_reports_a_healthy_status(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    status = gate.status()
    assert status["scale"] == 1.0 and status["hands"] == 0 and status["nearest_m"] is None
    assert status["fps"] > 0 and status["latency_ms"]["p50"] is not None and status["stale_s"] < 0.15
    assert set(status) == {"state", "scale", "nearest_m", "hands", "fps", "latency_ms", "stale_s", "reason", "updated"}


def test_a_hand_close_to_the_arm_stops_it_a_distant_one_slows_it_and_leaving_clears_without_latching(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    gate.detector.detections = [hand_at(0.30, 0.10)]                         # a middle distance: slow
    assert wait_for(lambda: gate.status()["state"] == "slow")
    assert 0 < gate.status()["scale"] < 1 and gate.status()["nearest_m"] > 0.15
    gate.detector.detections = [hand_at(0.04, 0.10)]                         # inside the stop distance
    assert wait_for(lambda: gate.status()["state"] == "stop") and gate.status()["scale"] == 0
    gate.detector.detections = []
    assert wait_for(lambda: gate.status()["state"] == "clear")               # a hand leaving does not latch
    assert not gate.safety.stopped


def test_a_stalled_camera_goes_stale_then_latches_a_stop_and_halts_the_arm(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    gate.camera.frozen = True
    assert wait_for(lambda: gate.status()["state"] in ("stale", "latched"))
    assert wait_for(lambda: gate.status()["state"] == "latched") and gate.safety.stopped and gate.arm._arm.halted


def test_a_manual_stop_latches_and_only_an_explicit_reset_builds_a_fresh_gate(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    gate.safety.stop()
    assert wait_for(lambda: gate.status()["state"] == "latched")
    assert gate.status()["state"] == "latched"                                # it does not resume by itself
    time.sleep(0.3)
    assert gate.status()["state"] == "latched"
    old_monitor = gate.monitor
    gate.reset()
    assert gate.monitor is not old_monitor and not gate.safety.stopped
    assert wait_for(lambda: gate.status()["state"] == "clear")


def test_a_gate_that_is_not_running_refuses_every_move():
    safety = Safety()
    g = HandGate(FakeCamera(), lambda: FakeHandDetector([]), CAL, FakeArm(), safety=safety)
    assert g.tracker(FakeTracker()).hands() == [NO_GATE_HAND]
    assert safety.allowed_speed([NO_GATE_HAND], ((0, 0.1, 0.05), (0.2, 0.1, 0.05))) == 0.0
    assert g.status()["state"] == "stale" and g.status()["stale_s"] is None


def test_a_camera_that_never_delivers_fails_to_start_instead_of_running_blind():
    class Silent(FakeCamera):
        def latest(self):
            return None
    g = HandGate(Silent(), lambda: FakeHandDetector([]), CAL, FakeArm(), warmup_s=0.2)
    with pytest.raises(RuntimeError, match="no frame"):
        g.start()
    assert g.monitor is None and g.tracker(FakeTracker()).hands() == [NO_GATE_HAND]


def test_the_wrapper_watches_the_segment_that_is_really_moving_and_delegates_everything_else():
    class Arm(FakeArm):
        def move_to(self, target, speed_scale):
            wrapped.seen = wrapped.path()
            return super().move_to(target, speed_scale)

        def action(self, name):
            return f"did {name}"
    wrapped = TargetTrackingArm(Arm())
    assert wrapped.path() == ((0.0, 0.10, 0.05),) * 2                           # idle: a point
    assert wrapped.move_to((0.2, 0.1, 0.05), 1.0)
    assert wrapped.seen == ((0.0, 0.10, 0.05), (0.2, 0.1, 0.05)) and wrapped.goal is None
    assert wrapped.action("reset") == "did reset"
    wrapped.halt()
    assert wrapped._arm.halted


# ---- through the runtime ---------------------------------------------------------------------------------------------------
def runtime_for(gate, script):
    objects = FakeTracker([TrackedObject("obj1", "red cube", (0.10, 0.10, 0.018))])
    model = FakeLLM(script)
    return Runtime(model, gate.arm, tracker=gate.tracker(objects), log=CallLog(), say=lambda _: None, safety=gate.safety, gate=gate), model


PICK = [ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'),)), ChatResult("Done.")]


def test_with_a_hand_near_the_pick_is_refused_and_the_model_is_told(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    runtime, _ = runtime_for(gate, list(PICK))
    gate.detector.detections = [hand_at(0.05, 0.10)]
    assert wait_for(lambda: gate.status()["state"] == "stop")
    result = runtime.command("pick up the red cube")
    assert result.steps and "blocked" in result.steps[0]["result"]
    assert gate.arm._arm.position == (0.0, 0.10, 0.05)                         # it did not move


def test_with_clear_hands_the_pick_goes_ahead_and_status_is_available_from_the_runtime(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    runtime, _ = runtime_for(gate, list(PICK))
    result = runtime.command("pick up the red cube")
    assert result.steps and result.steps[0]["result"] == "picked red cube"
    assert runtime.hand_gate_status()["state"] in ("clear", "slow")


def test_stop_through_the_runtime_latches_the_gate_and_reset_brings_it_back(gate):
    gate.start()
    assert wait_for(lambda: gate.status()["state"] == "clear")
    runtime, _ = runtime_for(gate, [ChatResult("Done.")])
    assert runtime.command("STOP").reply == "stopped"
    assert wait_for(lambda: gate.status()["state"] == "latched")
    runtime.reset()
    assert wait_for(lambda: gate.status()["state"] == "clear") and not runtime.safety.stopped


def test_a_runtime_without_a_gate_has_no_status():
    runtime = Runtime(FakeLLM([ChatResult("x")]), FakeArm(), tracker=FakeTracker(), log=CallLog(), say=lambda _: None)
    assert runtime.hand_gate_status() is None


# ---- status server ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def served():
    from http.server import ThreadingHTTPServer
    entries = [{"id": 1, "source": "Muse Spark"}, {"id": 2, "source": "SAM 3.1"}, {"id": 3, "source": "Safety"}]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: fake_gate_status(0.0), lambda: entries))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url, origin=None):
    request = urllib.request.Request(url, headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, json.loads(reply.read()), reply.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers


def test_the_status_server_serves_the_gate_and_the_timeline_read_only(served):
    status, body, headers = get(served + "/api/hand-gate", "http://127.0.0.1:8765")
    assert status == 200 and body["state"] == "clear" and headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8765"
    assert "Access-Control-Allow-Origin" not in get(served + "/api/hand-gate", "http://evil.example")[2]
    assert [e["id"] for e in get(served + "/api/timeline?since=1")[1]] == [2, 3]
    assert get(served + "/api/timeline?since=x")[0] == 400 and get(served + "/nope")[0] == 404
    request = urllib.request.Request(served + "/api/hand-gate", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 405


def test_the_fake_feed_cycles_through_every_state():
    states = [fake_gate_status(3.0 * i)["state"] for i in range(5)]
    assert states == ["clear", "slow", "stop", "latched", "stale"]
    assert fake_gate_status(0.0)["latency_ms"]["p95"] and fake_gate_status(9.0)["stale_s"] < 0.15 < fake_gate_status(12.0)["stale_s"]
