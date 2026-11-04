"""SamPeopleFeed: SAM masks become conservative marker-frame points in the voxel map. Real calibration geometry, fake SAM worker."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from lookandpick.adapters.sam_masks import Detection, detections_from_outputs  # noqa: E402
from lookandpick.sam_people import LAYERS, MAX_POINTS, SamPeopleFeed  # noqa: E402
from lookandpick.workspaces.voxels import Grid, Occupancy  # noqa: E402
from lookandpick.calibration import TableCalibration, detect_marker  # noqa: E402

K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]])

AREA = {"enabled": True, "centre": [0.0, 0.0], "width": 0.6, "depth": 0.6, "height": 1.6, "base": 0.0, "yaw": 0.0, "cell_size": 0.1,
        "ttl_ms": 2000, "hold_ms": 750, "margin": 0.0}
ALIGNMENT = {"id": "aruco-test", "canonical_from_marker": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
             "canonical_units_per_metre": 1.0, "status": "visual", "metric_inputs_complete": False}
FRAME = np.zeros((360, 640, 3), np.uint8)


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
    h = 0.06
    dst, _ = cv2.projectPoints(np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], float), rvec, tvec, K, None)
    frame = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32)), (1280, 720), borderValue=128)
    return TableCalibration.from_marker(detect_marker(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)), 0.12, K)


CAL = calibration()


class FakeClient:
    def __init__(self, by_prompt):
        self.by_prompt, self.calls, self.fail = by_prompt, [], False

    def segment(self, jpeg, prompt, max_objects=5, points=0):
        self.calls.append((prompt, points))
        if self.fail:
            from lookandpick.adapters.sam_client import SamWorkerError
            raise SamWorkerError("down")
        return list(self.by_prompt.get(prompt, []))


def pixel(x, y, z):
    return tuple(float(v) for v in CAL.table_to_pixel((x, y), z))


def detection(points, score=0.9):
    cx, cy = np.mean(points, axis=0)
    return Detection(score, (0, 0, 10, 10), (float(cx), float(cy)), (float(cx), float(cy)), 100, tuple(points))


def feed(by_prompt, **kw):
    mapping = Occupancy(Grid(AREA), "rev")
    client = FakeClient(by_prompt)
    f = SamPeopleFeed(lambda: ("ws", mapping.revision, "aruco-test"), lambda k, p: (posts.append(p), mapping.observe(p, ALIGNMENT)), lambda: FRAME,
                      client, CAL, **kw)
    posts = []
    f.posts = posts
    return f, mapping, client


def test_a_person_mask_marks_cells_at_the_projected_footprint_and_never_makes_anything_green():
    spot = (0.10, 0.05)
    f, mapping, client = feed({"person": [detection([pixel(*spot, 0.6)])]})
    assert f.tick() == 1 and client.calls == [("person", 150), ("hand", 150)]
    payload = f.posts[0]
    assert payload["kind"] == "person" and payload["frame"] == "marker_metres" and payload["source"] == "sam-people-person"
    assert payload["radius"] == 0.03 and payload["calibration_id"] == "aruco-test"
    heights = {round(p[2], 3) for p in payload["points"]}
    # The camera is 45 cm up, so a torso at 60 cm is ABOVE it: that ray points upward and can only meet planes above the camera.
    assert 0.6 in heights and heights <= set(LAYERS[0][2]) and min(heights) > 0.45
    at_mask_height = next(p for p in payload["points"] if abs(p[2] - 0.6) < 1e-9)
    assert at_mask_height[:2] == pytest.approx(spot, abs=0.005)                 # the right spot when the guess happens to be right
    counts = mapping.snapshot()["counts"]
    assert counts["occupied"] >= len(heights) and counts["free"] == 0


def test_hands_and_people_are_separate_observations_with_their_own_kind_and_source():
    f, mapping, _ = feed({"person": [detection([pixel(0.0, 0.0, 0.6)])], "hand": [detection([pixel(0.1, 0.1, 0.08)])]})
    assert f.tick() == 2
    assert [(p["kind"], p["source"]) for p in f.posts] == [("person", "sam-people-person"), ("hand", "sam-people-hand")]
    assert [p["sequence"] for p in f.posts] == [0, 1] and f.last_found == {"person": 1, "hand": 1}


def test_weak_detections_and_empty_scenes_send_nothing():
    f, mapping, _ = feed({"person": [detection([pixel(0, 0, 0.6)], score=0.1)]})
    assert f.tick() == 0 and mapping.snapshot()["counts"]["occupied"] == 0 and f.errors == 0
    f2, _, _ = feed({})
    assert f2.tick() == 0 and f2.last_found == {"person": 0, "hand": 0}


def test_rays_that_miss_the_table_are_skipped_and_the_point_count_is_capped():
    f, mapping, _ = feed({"hand": [detection([(320.0, -3000.0)])]})               # far above the horizon: an upward ray never reaches a hand-height plane
    assert f.tick() == 0 and f.errors == 0
    many = [detection([pixel(0.0, 0.0, 0.6)] * 2000)]
    f2, _, _ = feed({"person": many}, points_per_mask=2000)
    f2.tick()
    assert len(f2.posts[0]["points"]) <= MAX_POINTS


def test_a_dead_worker_or_no_workspace_or_no_frame_is_counted_not_raised():
    f, _, client = feed({"person": [detection([pixel(0, 0, 0.6)])]})
    client.fail = True
    assert f.tick() == 0 and f.errors == 1 and "down" in f.last_error
    f2 = SamPeopleFeed(lambda: None, lambda k, p: None, lambda: FRAME, FakeClient({}), CAL)
    assert f2.tick() == 0 and f2.errors == 0
    f3 = SamPeopleFeed(lambda: ("w", "r", "c"), lambda k, p: None, lambda: None, FakeClient({}), CAL)
    assert f3.tick() == 0 and f3.errors == 0
    with pytest.raises(ValueError):
        SamPeopleFeed(lambda: None, lambda k, p: None, lambda: FRAME, FakeClient({}), CAL, hz=30)


def test_the_worker_can_return_sampled_mask_pixels_in_original_image_scale():
    masks = np.zeros((1, 50, 100), bool)
    masks[0, 10:30, 20:60] = True
    (found,) = detections_from_outputs({"out_binary_masks": masks, "out_probs": np.array([0.9])}, (400, 200), sample_points=25)
    assert len(found.points) == 25
    assert all(80 <= x <= 240 and 40 <= y <= 120 for x, y in found.points)         # x4 scale: columns 20-60 -> 80-240, rows 10-30 -> 40-120
    assert detections_from_outputs({"out_binary_masks": masks}, (400, 200))[0].points == ()
