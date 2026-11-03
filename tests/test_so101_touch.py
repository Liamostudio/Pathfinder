"""Tip-touch calibration: the fit recovers known zero offsets and the marker-to-base frame from synthetic touches."""
import importlib.util
import json
import math
import random
from pathlib import Path

import pytest

from lookandpick.kinematics import BaseFrame, SO101Kinematics, URDF_LIMITS_DEG

spec = importlib.util.spec_from_file_location("so101_touch", Path(__file__).parent.parent / "scripts" / "so101_touch.py")
touch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(touch)

kin = SO101Kinematics()
TRUE_OFFSETS = [3.0, -2.0, 1.5]        # degrees to ADD to the readings on lift, elbow, wrist_flex
TRUE_TOOL = 0.012                      # the physical tip is 12 mm beyond the URDF's point
TRUE_PARAMS = TRUE_OFFSETS + [TRUE_TOOL]


def touching_pose(rng, height=0.0):
    """Joints (as READ) whose true tip, with the true offsets and tool, is `height` above the table: bisect shoulder_lift."""
    while True:
        base = {"shoulder_pan": rng.uniform(-50, 10), "elbow_flex": rng.uniform(20, 90), "wrist_flex": rng.uniform(-40, 60),
                "wrist_roll": 0.0}
        def z(lift):
            return touch._tip(kin, {**base, "shoulder_lift": lift}, TRUE_PARAMS)[2] - height
        lo, hi = -95.0, 20.0
        if z(lo) * z(hi) > 0:
            continue
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if z(lo) * z(mid) > 0 else (lo, mid)
        joints = {**base, "shoulder_lift": (lo + hi) / 2}
        x, y, _ = touch._tip(kin, joints, TRUE_PARAMS)
        if x > 0.08:
            return joints


def synthetic(frame: BaseFrame, plate=0.003):
    rng = random.Random(11)
    points = [{"label": f"table-{i}", "marker_xy": None, "joints": touching_pose(rng)} for i in range(8)]
    for label, xy in (("marker-centre", (0.0, 0.0)), ("marker-TL", (-0.06, 0.06)), ("marker-TR", (0.06, 0.06)),
                      ("marker-BR", (0.06, -0.06)), ("marker-BL", (-0.06, -0.06))):
        # pose whose true tip is at the marker point: take a random touching pose and find a pan/reach by IK on the TRUE model
        # is overkill; instead choose any touching pose and DEFINE its marker point from the base frame (a rigid transform)
        joints = touching_pose(rng, plate)
        x, y, _ = touch._tip(kin, joints, TRUE_PARAMS)
        mx, my, _ = frame.to_marker((x, y, 0.0))
        points.append({"label": label, "marker_xy": [mx, my], "joints": joints})
    return {"points": points}


def test_the_fit_recovers_the_true_zero_offsets_and_tool_shift():
    result = touch.analyse(synthetic(BaseFrame((0.02, -0.15, 0.0), 8.0)), log=lambda *_: None)
    fit = result["fit"]
    assert fit["fitted"] and fit["rms_after_mm"] < 0.2 < 1.0 < fit["rms_before_mm"]
    for name, true in zip(touch.FIT_JOINTS, TRUE_OFFSETS):
        assert fit["offsets_deg"][name] == pytest.approx(true, abs=0.3)
    assert fit["tool_shift_m"] == pytest.approx(TRUE_TOOL, abs=0.003)


def test_the_marker_points_give_the_base_offset_and_yaw():
    frame = BaseFrame((0.02, -0.15, 0.0), 8.0)
    base = touch.analyse(synthetic(frame), log=lambda *_: None)["base_frame"]
    assert base["fitted"]
    assert base["offset_m"] == pytest.approx(frame.offset[:2], abs=0.002) and base["yaw_deg"] == pytest.approx(8.0, abs=0.3)
    assert max(base["residuals_mm"]) < 1.5


def test_too_few_touches_say_so_instead_of_guessing():
    data = {"points": [{"label": "table-1", "marker_xy": None,
                        "joints": {"shoulder_pan": 0, "shoulder_lift": -30, "elbow_flex": 40, "wrist_flex": 10, "wrist_roll": 0}}]}
    result = touch.analyse(data, log=lambda *_: None)
    assert not result["fit"]["fitted"] and "at least 5" in result["fit"]["reason"]
    assert not result["base_frame"]["fitted"]


def test_alike_touches_are_flagged_as_poorly_determined():
    same = {"shoulder_pan": -20, "shoulder_lift": -40, "elbow_flex": 50, "wrist_flex": 5, "wrist_roll": 0}
    lines = []
    touch.analyse({"points": [{"label": f"table-{i}", "marker_xy": None, "joints": dict(same, shoulder_pan=same["shoulder_pan"] + i)}
                              for i in range(6)]}, log=lines.append)
    assert any("WARNING" in line or "No offset fit" in line for line in lines)


class StillBus:
    def __init__(self, jitter=0.0):
        self.reads, self.jitter = 0, jitter

    def sync_read(self, name, normalize=True):
        self.reads += 1
        wobble = self.jitter * (self.reads % 2)
        return {"shoulder_pan": 0.0 + wobble, "shoulder_lift": -30.0, "elbow_flex": 40.0, "wrist_flex": 10.0, "wrist_roll": 0.0,
                "gripper": 1.0}


def test_sampling_averages_when_still_and_refuses_when_the_arm_moves():
    assert touch.sample_still(StillBus(), sleep=lambda _: None)["elbow_flex"] == pytest.approx(40.0)
    with pytest.raises(touch.MovedWhileReading, match="shoulder_pan"):
        touch.sample_still(StillBus(jitter=1.0), sleep=lambda _: None)


def test_capture_saves_each_point_skips_and_finishes_and_never_writes(tmp_path):
    answers = iter(["", "s", "", "q"])
    out = tmp_path / "t" / "touches.json"

    class ReadOnly(StillBus):
        def write(self, *a, **k):
            raise AssertionError("capture must not write to the motors")
        sync_write = enable_torque = disable_torque = write

    points = touch.capture(ReadOnly(), touch.PLAN, str(out), ask=lambda _: next(answers), log=lambda *_: None,
                           sleep=lambda _: None, robot_id="follower")
    saved = json.loads(out.read_text())
    assert [p["label"] for p in points] == ["table-1", "table-3"] == [p["label"] for p in saved["points"]]
    assert saved["robot_id"] == "follower"
    assert any(label == "marker-centre" and xy == (0.0, 0.0) for label, xy, _ in touch.PLAN)


def test_the_variety_plan_touches_each_spot_at_three_tilts_and_append_keeps_earlier_points(tmp_path):
    assert len(touch.PLAN_VARIETY) == 9 and len({label for label, _, _ in touch.PLAN_VARIETY}) == 9
    assert {label.split("-")[0] for label, _, _ in touch.PLAN_VARIETY} == {"spot1", "spot2", "spot3"}
    earlier = [{"label": "table-1", "marker_xy": None, "joints": StillBus().sync_read("Present_Position")}]
    answers = iter(["", "q"])
    points = touch.capture(StillBus(), touch.PLAN_VARIETY, str(tmp_path / "t.json"), ask=lambda _: next(answers),
                           log=lambda *_: None, sleep=lambda _: None, existing=earlier)
    assert [p["label"] for p in points] == ["table-1", "spot1-tilt0"]
