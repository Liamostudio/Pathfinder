"""The commissioned SO-101 profile: built from Liam's real calibration, planned and tested with NO hardware."""
import json
import math
import shutil
from dataclasses import replace

import pytest

from lookandpick.adapters.so101 import JOINTS, SO101Arm, SO101Limits
from lookandpick.adapters.so101_profile import (CALIBRATION_DIR, LEROBOT_CALIBRATION, REST_READING, TOUCH_CALIBRATION, MarkerFrameArm,
                                                calibrated_kinematics, commissioned_limits, dry_run_arm, lerobot_ranges_deg)
from lookandpick.kinematics import BaseFrame

pytestmark = pytest.mark.skipif(not (LEROBOT_CALIBRATION.exists() and TOUCH_CALIBRATION.exists()),
                                reason="the real calibration files are not in this checkout")
TICK_DEG = 360 / 4095


def test_limits_come_from_the_calibrated_ranges_with_a_margin_and_are_commissioned():
    limits = commissioned_limits()
    assert limits.commissioned and limits.cartesian_step_m > 0
    for name, (lo, hi) in lerobot_ranges_deg().items():
        if name != "wrist_roll":
            assert limits.joint_ranges[name] == pytest.approx((lo + 3.0, hi - 3.0))
    assert limits.joint_ranges["gripper"][1] <= 50.0 and limits.workspace_min[2] >= 0.01


def test_development_limits_still_refuse_wide_ranges():
    with pytest.raises(ValueError):
        SO101Limits(joint_ranges={**SO101Limits().joint_ranges, "shoulder_lift": (-100.0, 100.0)})


def test_the_rest_pose_is_accepted_even_though_it_sits_on_the_ends_of_two_ranges():
    arm, inner = dry_run_arm()                       # raises if Rest were refused as an observed pose
    x, y, z = inner.position
    assert 0.01 <= z <= 0.03 and math.hypot(x, y) > 0.08
    assert arm.position == pytest.approx(arm.frame.to_marker(inner.position))
    assert abs(REST_READING["elbow_flex"]) > inner.limits.joint_ranges["elbow_flex"][1]      # Rest really is past the target range


def test_it_refuses_to_commission_without_both_files_or_with_a_poor_validation(tmp_path):
    with pytest.raises(FileNotFoundError):
        commissioned_limits(tmp_path / "nope.json", TOUCH_CALIBRATION)
    bad = json.loads(TOUCH_CALIBRATION.read_text())
    bad["held_out"]["heights, 4 parameters"]["median_mm"] = 9.0
    poor = tmp_path / "poor.json"
    poor.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="not validated"):
        commissioned_limits(LEROBOT_CALIBRATION, poor)
    no_frame = json.loads(TOUCH_CALIBRATION.read_text())
    no_frame["base_frame"] = None
    nf = tmp_path / "nf.json"
    nf.write_text(json.dumps(no_frame))
    with pytest.raises(ValueError, match="base frame"):
        commissioned_limits(LEROBOT_CALIBRATION, nf)


@pytest.mark.parametrize("target", [(0.0, 0.0, 0.05), (0.05, 0.0, 0.03), (0.0, 0.1, 0.05), (0.1, -0.05, 0.04)])
def test_dry_run_moves_follow_a_straight_line_within_every_limit(target):
    arm, inner = dry_run_arm()
    start = arm.position
    assert arm.move_to(target, 1.0)
    assert math.dist(arm.position, target) <= 0.002
    ticks = [e[1] for e in inner.log if e[0] == "dry_run"]
    previous = dict(REST_READING)
    for joints in ticks:
        assert max(abs(joints[n] - previous[n]) for n in JOINTS) <= 0.5 + 1e-9                         # slow, small steps
        for name, value in joints.items():
            lo, hi = inner.limits.joint_ranges[name]
            assert lo - inner.limits.observation_margin_deg <= value <= hi + inner.limits.observation_margin_deg
        assert inner.limits.contains(inner.kinematics.forward(joints))
        previous = joints
    # the tip stays near the straight line from start to target (joint interpolation between 1 cm waypoints)
    for joints in ticks:
        p = arm.frame.to_marker(inner.kinematics.forward(joints))
        d = [b - a for a, b in zip(start, target)]
        t = max(0.0, min(1.0, sum((p[i] - start[i]) * d[i] for i in range(3)) / max(1e-12, sum(v * v for v in d))))
        assert math.dist(p, tuple(start[i] + t * d[i] for i in range(3))) < 0.02


def marker(base_point):
    return BaseFrame.from_calibration(str(TOUCH_CALIBRATION)).to_marker(base_point)


@pytest.mark.parametrize("name,target", [
    ("below the table limit", (0.0, 0.0, 0.0)),
    ("within 8 cm of the pan axis", marker((0.03, 0.0, 0.10))),
    ("outside the envelope", (0.5, 0.5, 0.1)),
    ("not finite", (float("nan"), 0.0, 0.05)),
])
def test_bad_targets_are_refused_with_no_motion_and_do_not_latch_a_stop(name, target):
    arm, inner = dry_run_arm()
    before = dict(inner.joints)
    assert not arm.move_to(target, 1.0)
    assert inner.joints == before and not any(e[0] == "dry_run" for e in inner.log)
    assert arm.move_to((0.05, 0.0, 0.03), 1.0)            # a refusal is not a halt: the next valid move works


def test_an_unreachable_waypoint_refuses_the_whole_plan_before_any_motion():
    arm, inner = dry_run_arm()
    # pan is limited to +-70 degrees: a spot behind the base axis is reachable by the box but not by the joints
    behind = marker((-0.10, 0.25, 0.05))
    before = dict(inner.joints)
    assert not arm.move_to(behind, 1.0)
    assert inner.joints == before and not any(e[0] == "dry_run" for e in inner.log)


def test_marker_frame_arm_converts_both_ways_and_delegates():
    arm, inner = dry_run_arm()
    assert arm.position == pytest.approx(arm.frame.to_marker(inner.position))
    assert isinstance(arm, MarkerFrameArm) and arm.joints is inner.joints
    arm.set_speed_scale(0.5)
    assert inner._scale == 0.5
    arm.halt()
    assert not arm.move_to((0.05, 0.0, 0.03), 1.0) and not arm.grip(True)         # halt latches through the wrapper


class QuantisedBus:
    """Stands in for the real arm: observations come back rounded to the motors' 0.088 degree ticks."""
    def __init__(self):
        self.goal = dict(REST_READING)
        self.writes, self.halted, self.gripper_cap = [], False, None

    def read_positions(self):
        return {n: round(v / TICK_DEG) * TICK_DEG for n, v in self.goal.items()}

    def send_positions(self, joints):
        self.writes.append(dict(joints))
        self.goal = dict(joints)

    def halt(self):
        self.halted = True

    def set_gripper_torque_limit(self, raw):
        self.gripper_cap = raw


def test_a_move_completes_on_a_bus_that_reports_in_motor_ticks():
    """With the old 0.0001 degree completion tolerance this could never succeed on the real arm."""
    bus = QuantisedBus()
    limits = commissioned_limits()
    inner = SO101Arm(bus=bus, kinematics=calibrated_kinematics(TOUCH_CALIBRATION, limits), limits=limits, dry_run=False,
                     i_am_present=True, presence_check=lambda _: None)
    arm = MarkerFrameArm(inner, BaseFrame.from_calibration(str(TOUCH_CALIBRATION)))
    assert bus.gripper_cap == limits.gripper_torque_raw
    assert arm.move_to((0.05, 0.0, 0.03), 1.0)
    assert math.dist(arm.position, (0.05, 0.0, 0.03)) <= 0.002 and not bus.halted
    observed_start = QuantisedBus().read_positions()
    previous = dict(observed_start)
    for sent in bus.writes:
        assert max(abs(sent[n] - previous[n]) for n in JOINTS) <= 0.5 + 0.1             # one small step at a time
        assert sent["gripper"] == pytest.approx(observed_start["gripper"])               # the gripper stays where it was observed
        previous = sent


def test_the_default_limits_are_unchanged_so_nothing_else_moves_differently():
    limits = SO101Limits()
    assert (limits.cartesian_step_m, limits.completion_tolerance_deg, limits.min_reach_m, limits.observation_margin_deg) == (0.0, 1e-4, 0.0, 0.0)


def test_the_planner_script_reports_accepted_and_refused_plans_without_touching_hardware():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("so101_plan", Path(__file__).parent.parent / "scripts" / "so101_plan.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    lines = []
    good = planner.plan((0.05, 0.0, 0.03), log=lines.append)
    assert good["ok"] and good["ticks"] > 0 and any("ACCEPTED" in line for line in lines) and any("shoulder_lift" in line for line in lines)
    lines.clear()
    bad = planner.plan((0.5, 0.5, 0.1), log=lines.append)
    assert not bad["ok"] and bad["ticks"] == 0 and any("Nothing moved" in line for line in lines)
