"""Language to target: SAM masks -> detections -> table positions, the worker's HTTP surface, and the Spark scene labeller.

Everything runs without a GPU or a network: a synthetic camera at a known pose, a fake SAM worker and a fake chat model."""
import base64
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.adapters.sam_client import SamClient, SamWorkerError  # noqa: E402
from lookandpick.adapters.sam_masks import Detection, detections_from_outputs  # noqa: E402
from lookandpick.adapters.sam_tracker import SamTracker  # noqa: E402
from lookandpick.calibration import TableCalibration, detect_marker  # noqa: E402
from lookandpick.models.base import ChatResult, FakeLLM  # noqa: E402
from lookandpick.models.meta import _summary  # noqa: E402
from lookandpick.scene_labeller import SceneLabelError, SceneLabeller, parse_labels  # noqa: E402

MARKER_M = 0.12
K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]])
SIZE = (1280, 720)


def look_at(pos):
    pos = np.asarray(pos, float)
    z = -pos / np.linalg.norm(pos)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    rot = np.vstack([x, np.cross(z, x), z])
    return cv2.Rodrigues(rot)[0].reshape(3), -rot @ pos


def calibrated(pos=(0.0, -0.55, 0.45)):
    rvec, tvec = look_at(pos)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side, pad = 600, 100
    img = cv2.copyMakeBorder(cv2.aruco.generateImageMarker(d, 0, side), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    src = np.float32([[pad, pad], [pad + side, pad], [pad + side, pad + side], [pad, pad + side]])
    h = MARKER_M / 2
    dst, _ = cv2.projectPoints(np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], float), rvec, tvec, K, None)
    frame = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32)), SIZE, borderValue=128)
    corners = detect_marker(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    assert corners is not None
    return TableCalibration.from_marker(corners, MARKER_M, K), rvec, tvec


def pixel_of(point, rvec, tvec):
    return cv2.projectPoints(np.array(point, float).reshape(1, 3), rvec, tvec, K, None)[0].reshape(2)


# ---- masks -------------------------------------------------------------------------------------------------------------
def test_detections_are_scaled_to_the_original_image_and_sorted_by_score():
    masks = np.zeros((3, 100, 200), bool)                 # a low-resolution mask stack for a 800 x 400 image
    masks[0, 20:40, 50:70] = True                         # 20 x 20 mask pixels
    masks[1, 60, 10] = True                               # 1 mask pixel = 16 image pixels, under the 20 minimum: dropped
    masks[2, 10:30, 150:190] = True
    found = detections_from_outputs({"out_binary_masks": masks, "out_probs": np.array([0.6, 0.99, 0.9])}, (800, 400))
    assert [round(d.score, 2) for d in found] == [0.9, 0.6]
    first = found[1]                                       # the 0.6 one
    assert first.box == (200.0, 80.0, 280.0, 160.0)        # x4 horizontally, x4 vertically
    assert first.centroid == pytest.approx((240.0, 120.0)) and first.bottom[1] == 160.0 and first.area == 20 * 20 * 16


def test_empty_missing_and_torch_like_outputs_are_handled():
    assert detections_from_outputs(None, (10, 10)) == [] and detections_from_outputs({}, (10, 10)) == []
    assert detections_from_outputs({"out_binary_masks": np.zeros((0, 4, 4), bool)}, (10, 10)) == []

    class TorchLike:
        def __init__(self, a):
            self.a = a

        def cpu(self):
            return self.a
    m = np.zeros((1, 1, 50, 50), bool)
    m[0, 0, 10:30, 10:30] = True
    assert len(detections_from_outputs({"out_binary_masks": TorchLike(m), "out_probs": TorchLike(np.array([0.8]))}, (50, 50))) == 1


# ---- tracker -----------------------------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, by_prompt):
        self.by_prompt, self.calls, self.fail = by_prompt, [], False

    def segment(self, jpeg, prompt, max_objects=5):
        self.calls.append((prompt, len(jpeg)))
        if self.fail:
            raise SamWorkerError("down")
        return list(self.by_prompt.get(prompt, []))


def det(pixel, score=0.9):
    return Detection(score, (pixel[0] - 10, pixel[1] - 10, pixel[0] + 10, pixel[1] + 10), (float(pixel[0]), float(pixel[1])),
                     (float(pixel[0]), float(pixel[1]) + 10), 400)


def tracker_for(by_prompt, scale=1.0, **kw):
    cal, rvec, tvec = calibrated()
    return SamTracker(FakeClient(by_prompt), cal, SIZE, **kw), rvec, tvec, np.zeros((int(720 * scale), int(1280 * scale), 3), np.uint8)


def test_a_detection_becomes_a_table_position_within_5_mm_of_the_truth():
    truth = (0.08, 0.05, 0.015)                           # the centre of a 3 cm object on the table
    cal, rvec, tvec = calibrated()
    tracker = SamTracker(FakeClient({"red cube": [det(pixel_of(truth, rvec, tvec))]}), cal, SIZE, object_height_m=0.03)
    found = tracker.refresh(np.zeros((720, 1280, 3), np.uint8), ["red cube"])
    assert len(found) == 1 and found[0].label == "red cube" and found[0].id == "red-cube-1"
    assert found[0].position[:2] == pytest.approx(truth[:2], abs=0.005)
    assert found[0].position[2] == pytest.approx(0.015) and not found[0].held and tracker.hands() == []


def test_a_resized_frame_still_gives_the_same_position():
    truth = (-0.06, 0.04, 0.015)
    cal, rvec, tvec = calibrated()
    u, v = pixel_of(truth, rvec, tvec)
    tracker = SamTracker(FakeClient({"mug": [det((u / 2, v / 2))]}), cal, SIZE)       # the worker saw a half-size frame
    found = tracker.refresh(np.zeros((360, 640, 3), np.uint8), ["mug"])
    assert found[0].position[:2] == pytest.approx(truth[:2], abs=0.005)


def test_ids_stay_stable_while_an_object_moves_a_little_and_new_ones_get_new_ids():
    cal, rvec, tvec = calibrated()
    here, nudged, far = (0.05, 0.02, 0.015), (0.06, 0.02, 0.015), (-0.10, -0.05, 0.015)
    client = FakeClient({"red cube": [det(pixel_of(here, rvec, tvec))]})
    tracker, frame = SamTracker(client, cal, SIZE), np.zeros((720, 1280, 3), np.uint8)
    first = tracker.refresh(frame, ["red cube"])[0].id
    client.by_prompt["red cube"] = [det(pixel_of(nudged, rvec, tvec))]
    assert tracker.refresh(frame, ["red cube"])[0].id == first                              # moved 1 cm: same object
    client.by_prompt["red cube"] = [det(pixel_of(nudged, rvec, tvec)), det(pixel_of(far, rvec, tvec), 0.8)]
    ids = sorted(o.id for o in tracker.refresh(frame, ["red cube"]))
    assert ids == sorted([first, "red-cube-2"])


def test_weak_offtable_and_failed_detections_never_become_objects():
    cal, rvec, tvec = calibrated()
    good = det(pixel_of((0.05, 0.02, 0.015), rvec, tvec))
    sky = det((640, 5))                                                                      # above the horizon: the ray misses the table
    far_away = det(pixel_of((0.9, 0.9, 0.015), rvec, tvec))
    weak = det(pixel_of((0.0, 0.0, 0.015), rvec, tvec), score=0.1)
    client = FakeClient({"cube": [good, sky, far_away, weak]})
    tracker, frame = SamTracker(client, cal, SIZE, min_score=0.35), np.zeros((720, 1280, 3), np.uint8)
    assert len(tracker.refresh(frame, ["cube"])) == 1
    client.fail = True
    with pytest.raises(SamWorkerError):
        tracker.refresh(frame, ["cube"])
    assert len(tracker.objects()) == 1                                                       # the last good answer is kept, not invented over


def test_the_tracker_reports_how_old_its_answer_is():
    now = [100.0]
    cal, rvec, tvec = calibrated()
    tracker = SamTracker(FakeClient({}), cal, SIZE, clock=lambda: now[0])
    assert tracker.age_s() is None
    tracker.refresh(np.zeros((720, 1280, 3), np.uint8), ["cube"])
    now[0] = 103.5
    assert tracker.age_s() == pytest.approx(3.5)


# ---- client and worker server -------------------------------------------------------------------------------------------
def test_the_client_only_talks_to_loopback_and_maps_failures():
    for bad in ("http://example.com:8767", "https://127.0.0.1:8767", "ftp://localhost"):
        with pytest.raises(ValueError):
            SamClient(bad)

    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(SamWorkerError, match="not reachable"):
        SamClient(opener=refuse).health()


def worker_module():
    spec = importlib.util.spec_from_file_location("sam_worker", Path(__file__).parent.parent / "scripts" / "sam_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeModel:
    def segment(self, jpeg, prompt, max_objects):
        masks = np.zeros((1, 72, 128), bool)
        masks[0, 30:40, 60:70] = True
        return {"out_binary_masks": masks, "out_probs": np.array([0.93])}, (1280, 720)


@pytest.fixture
def served():
    worker = worker_module()
    from lookandpick.adapters import sam_masks
    server = ThreadingHTTPServer(("127.0.0.1", 0), worker.make_handler(FakeModel(), sam_masks))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_worker_serves_health_and_segmentation_to_the_real_client(served):
    client = SamClient(served)
    assert client.health()["ok"] is True
    found = client.segment(b"\xff\xd8 not decoded by the fake model", "red cube")
    assert len(found) == 1 and found[0].score == pytest.approx(0.93) and found[0].centroid == pytest.approx((650.0, 350.0))


def post(url, body):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_worker_rejects_bad_requests_without_dying(served):
    good = base64.b64encode(b"x").decode()
    assert post(served + "/segment", b"not json") == 400
    assert post(served + "/segment", json.dumps({"prompt": "cube", "image_b64": "!!!not base64!!!"}).encode()) == 400
    assert post(served + "/segment", json.dumps({"prompt": "", "image_b64": good}).encode()) == 400
    assert post(served + "/segment", json.dumps({"prompt": "x" * 200, "image_b64": good}).encode()) == 400
    assert post(served + "/elsewhere", b"{}") == 404
    assert SamClient(served).health()["ok"]                                                # still alive


# ---- scene labeller ----------------------------------------------------------------------------------------------------
def test_labels_are_validated_hard_because_they_become_sam_prompts():
    assert parse_labels('Sure! {"objects": ["Red cube", "blue  mug", "red cube", "hand", "a person", "robot arm", 7, "x", "<script>"]}') == ["red cube", "blue mug"]
    assert parse_labels('{"objects": []}') == []
    assert len(parse_labels(json.dumps({"objects": [f"toy number {c}" for c in "abcdefghij"]}), max_objects=4)) == 4
    for bad in ("no json here", '{"objects": "red cube"}', '{"things": []}', "{broken"):
        with pytest.raises(SceneLabelError):
            parse_labels(bad)


def test_the_labeller_sends_the_frame_as_an_image_and_returns_clean_prompts():
    model = FakeLLM([ChatResult('{"objects": ["red cube", "green block"]}')])
    labels = SceneLabeller(model).label(np.zeros((90, 160, 3), np.uint8))
    assert labels == ["red cube", "green block"]
    content = model.requests[0]["messages"][0]["content"]
    assert content[0]["type"] == "text" and content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_the_call_log_summary_never_contains_image_bytes():
    summary = _summary([{"role": "user", "content": [{"type": "text", "text": "what is on the table?"},
                                                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 5000}}]}])
    assert summary == "what is on the table? [image]" and "AAAA" not in summary


def test_an_empty_reply_says_the_model_ran_out_of_tokens_and_the_budget_is_generous():
    with pytest.raises(SceneLabelError, match="ran out of tokens"):
        parse_labels("")
    seen = {}

    class Spy(FakeLLM):
        def chat(self, messages, tools=None, max_tokens=1500):
            seen["max_tokens"] = max_tokens
            return ChatResult('{"objects": []}')
    SceneLabeller(Spy([])).label(np.zeros((9, 16, 3), np.uint8))
    assert seen["max_tokens"] >= 2000
