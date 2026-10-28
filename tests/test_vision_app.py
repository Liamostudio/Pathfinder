"""Pathfinder Vision: what the panel says, the pipeline, recording. Fake camera and detectors, no models needed."""
import sys
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "pathfinder-vision"))

from pathfinder_vision import ui  # noqa: E402
from pathfinder_vision.detectors import Detection  # noqa: E402
from pathfinder_vision.pipeline import Pipeline  # noqa: E402
from pathfinder_vision.recorder import Recorder  # noqa: E402

FRAME = np.full((360, 640, 3), 90, np.uint8)


class FakeCamera:
    def latest(self):
        self.n = getattr(self, "n", 0) + 1
        time.sleep(0.003)
        return self.n, time.monotonic(), FRAME


class FakeDetector:
    def __init__(self, kind, count):
        self.kind, self.count, self.calls = kind, count, 0

    def detect(self, frame, ts):
        self.calls += 1
        return [Detection(self.kind, f"{self.kind.upper()} {i}", 0.9, (10, 10, 100, 100), [(20.0, 20.0)]) for i in range(self.count)]

    def close(self):
        pass


def wait_snapshot(pipe, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        snap = pipe.snapshot()
        if snap:
            return snap
        time.sleep(0.01)
    raise AssertionError("no snapshot")


def make(**factories):
    pipe = Pipeline(FakeCamera(), factories, people_every=2)
    pipe.start()
    return pipe


def test_panel_states_what_runs_and_what_it_sees():
    pipe = make(hands=lambda: FakeDetector("hand", 2), **{"person-box": lambda: FakeDetector("person", 1)})
    pipe.cycle_people()  # boxes
    time.sleep(0.3)
    frame, dets, rows, fps, ms = wait_snapshot(pipe)
    state = {"camera": {"name": "Test cam", "width": 640, "height": 360}, "fps": fps, "pipeline_ms": ms,
             "detectors": rows, "detections": dets, "recording": None}
    text = "\n".join(t for t, _, _ in ui.panel_lines(state))
    pipe.close()
    for expected in ("Test cam", "640x360", "RUNNING", "HANDS: ON", "PEOPLE: BOXES: ON", "EfficientDet-Lite0",
                     "DETECTING NOW", "HAND 0", "HAND 1", "PERSON 0", "NOT RUNNING", "CPU"):
        assert expected in text, expected


def test_off_detectors_are_shown_as_off_and_not_run():
    hands = FakeDetector("hand", 1)
    pipe = make(hands=lambda: hands)
    pipe.hands_on = False
    time.sleep(0.2)
    _, dets, rows, _, _ = wait_snapshot(pipe)
    pipe.close()
    assert dets == [] and hands.calls == 0
    assert [r["status"] for r in rows] == ["off", "off"] and rows[1]["title"] == "PEOPLE"


def test_people_detector_runs_every_other_frame_hands_every_frame():
    hands, people = FakeDetector("hand", 1), FakeDetector("person", 1)
    pipe = make(hands=lambda: hands, **{"person-skeleton": lambda: people})
    pipe.cycle_people(); pipe.cycle_people()
    time.sleep(0.5)
    pipe.close()
    assert hands.calls > 10 and 0.35 < people.calls / hands.calls < 0.65


def test_missing_model_is_reported_in_the_panel_not_a_crash():
    def missing():
        raise FileNotFoundError("hand_landmarker.task")
    pipe = make(hands=missing)
    time.sleep(0.2)
    _, _, rows, _, _ = wait_snapshot(pipe)
    pipe.close()
    assert rows[0]["status"] == "missing" and "download-models" in rows[0]["note"]


def test_detector_exception_is_reported():
    class Broken(FakeDetector):
        def detect(self, frame, ts):
            raise RuntimeError("boom")
    pipe = make(hands=lambda: Broken("hand", 1))
    time.sleep(0.2)
    _, _, rows, _, _ = wait_snapshot(pipe)
    pipe.close()
    assert rows[0]["status"] == "error" and "boom" in rows[0]["note"]


def test_render_has_view_and_panel_and_a_detecting_banner():
    pipe = make(hands=lambda: FakeDetector("hand", 1))
    time.sleep(0.2)
    frame, dets, rows, fps, ms = wait_snapshot(pipe)
    pipe.close()
    state = {"camera": {"name": "c", "width": 640, "height": 360}, "fps": fps, "pipeline_ms": ms, "detectors": rows,
             "detections": dets, "recording": "00:05"}
    image = ui.render(cv2, frame, dets, state, view_w=800, panel_w=400)
    assert image.shape == (max(450, 520), 1200, 3)
    assert image[:30, :800].std() > 0 and (image[:, 800:] != ui.PANEL_BG).any()


def test_recorder_keeps_real_time_with_uneven_frames(tmp_path):
    now = [0.0]
    rec = Recorder(tmp_path, fps=10, clock=lambda: now[0])
    img = np.zeros((48, 64, 3), np.uint8)
    path = rec.start((64, 48))
    rec.add(img)
    now[0] = 1.0  # a one-second gap
    rec.add(img)
    rec.stop()
    assert rec.written == 11 and path.exists() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT) >= 10
    cap.release()


def test_dropdown_geometry_and_hit_testing():
    geo = ui.dropdown_geometry(1024, 480, 3)
    assert len(geo["items"]) == 3
    bx0, by0, bx1, by1 = geo["box"]
    assert ui.inside(geo["box"], bx0 + 5, by0 + 5) and not ui.inside(geo["box"], bx1, by1)
    first, second = geo["items"][0], geo["items"][1]
    assert first[3] == second[1] and first[1] == by1  # items stack directly under the box


def test_render_shows_the_dropdown_and_its_choices():
    state = {"camera": {"name": "Insta360 Ace Pro", "width": 640, "height": 360}, "fps": 30.0, "pipeline_ms": 20.0,
             "detectors": [], "detections": [], "recording": None,
             "dropdown": {"open": True, "items": [(0, "Integrated Camera"), (2, "Insta360 Ace Pro")], "selected": 2, "status": ""}}
    closed = dict(state, dropdown=dict(state["dropdown"], open=False))
    a = ui.render(cv2, FRAME, [], state, view_w=800, panel_w=480)
    b = ui.render(cv2, FRAME, [], closed, view_w=800, panel_w=480)
    geo = ui.dropdown_geometry(800, 480, 2)["items"][0]
    assert (a[geo[1]:geo[3], geo[0]:geo[2]] != b[geo[1]:geo[3], geo[0]:geo[2]]).any()


def test_pipeline_switches_camera_and_starts_detectors_afresh():
    class Cam(FakeCamera):
        closed = False

        def __init__(self, tag):
            self.tag = tag

        def close(self):
            self.closed = True

    first, second = Cam("a"), Cam("b")
    det = FakeDetector("hand", 1)
    made = []
    pipe = Pipeline(first, {"hands": lambda: made.append(1) or det}, people_every=2)
    pipe.start()
    wait_snapshot(pipe)
    pipe.set_camera(second)
    time.sleep(0.4)
    snap = wait_snapshot(pipe)
    pipe.close()
    assert first.closed and pipe.camera is second and len(made) == 2 and snap[1]


def test_list_cameras_keeps_only_the_first_node_of_each_device(tmp_path):
    from pathfinder_vision.camera import list_cameras
    for n, (idx, name) in enumerate([("0", "Built-in"), ("1", "Built-in"), ("0", "Ace Pro"), ("1", "Ace Pro")]):
        d = tmp_path / f"video{n}"
        d.mkdir()
        (d / "index").write_text(idx)
        (d / "name").write_text(name)
    assert list_cameras(str(tmp_path)) == [(0, "Built-in"), (2, "Ace Pro")]
