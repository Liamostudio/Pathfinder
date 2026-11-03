"""SO-101 kinematics: the constants match the vendored URDF, FK matches the simulator's MuJoCo FK, IK round-trips."""
import math
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lookandpick.kinematics import (CHAIN, JOINTS, SOLVED, URDF_LIMITS_DEG, BaseFrame, SO101Kinematics, _URDF_CHAIN, _URDF_TCP)

ROOT = Path(__file__).resolve().parent.parent
URDF = ROOT / "simulation" / "assets" / "so101" / "so101_new_calib.urdf"
GRIPPER_SEED = {"wrist_roll": 0.0, "gripper": 5.0}
kin = SO101Kinematics()


def pose(rng, spread=None, centre=None):
    q = {n: rng.uniform(*URDF_LIMITS_DEG[n]) for n in SOLVED}
    q.update(wrist_roll=rng.uniform(-60, 60), gripper=5.0)
    return q


def numbers(text):
    return tuple(float(v) for v in text.split())


def test_the_constants_are_the_vendored_urdf_chain():
    joints = {j.get("name"): j for j in ET.parse(URDF).getroot().iter("joint") if j.get("type") in ("revolute", "fixed")}
    for name, xyz, rpy, (lo, hi) in _URDF_CHAIN:
        j = joints[name]
        assert numbers(j.find("origin").get("xyz")) == pytest.approx(xyz, abs=1e-6)
        assert numbers(j.find("origin").get("rpy")) == pytest.approx(rpy, abs=1e-5)
        assert numbers(j.find("axis").get("xyz")) == (0.0, 0.0, 1.0)
        assert (float(j.find("limit").get("lower")), float(j.find("limit").get("upper"))) == pytest.approx((lo, hi), abs=1e-6)
    tcp = joints["gripper_frame_joint"]
    assert numbers(tcp.find("origin").get("xyz")) == pytest.approx(_URDF_TCP[0], abs=1e-6)
    assert numbers(tcp.find("origin").get("rpy")) == pytest.approx(_URDF_TCP[1], abs=1e-5)


def test_zero_pose_is_where_mujoco_says_it_is():
    x, y, z = kin.forward({n: 0.0 for n in CHAIN})
    assert (x, y, z) == pytest.approx((0.391, 0.0, 0.226), abs=1e-3)


def test_fk_matches_the_simulators_mujoco_fk():
    mujoco = pytest.importorskip("mujoco")
    np = pytest.importorskip("numpy")
    sys.path.insert(0, str(ROOT / "simulation"))
    from sim_env.model import ARM_BASE_OFFSET, build_model
    model = build_model()
    data = mujoco.MjData(model)
    tcp = model.site("tcp").id
    addr = [model.jnt_qposadr[model.joint(n).id] for n in CHAIN]
    rng = random.Random(3)
    for _ in range(200):
        q = {n: rng.uniform(*URDF_LIMITS_DEG[n]) for n in CHAIN}
        for n, a in zip(CHAIN, addr):
            data.qpos[a] = math.radians(q[n])
        mujoco.mj_forward(model, data)
        reference = np.array(data.site_xpos[tcp]) - np.array(ARM_BASE_OFFSET)
        assert np.linalg.norm(reference - np.array(kin.forward(q))) < 1e-5


def working_pose(rng):
    """A random pose whose tip is in front of the base and above the table: where the arm is actually asked to work."""
    while True:
        q = pose(rng)
        x, _, z = kin.forward(q)
        if x > 0.08 and z >= 0.0:
            return q


def test_ik_round_trips_in_the_working_area_from_a_seed_within_45_degrees():
    """Targets behind the base, under its plane or almost on the pan axis can fail (the pan angle is degenerate there:
    about 0.6% of random poses over the whole joint space); none of the working area does."""
    rng = random.Random(7)
    for _ in range(300):
        q = working_pose(rng)
        target = kin.forward(q)
        seed = dict(q)
        for n in SOLVED:
            lo, hi = URDF_LIMITS_DEG[n]
            seed[n] = min(max(q[n] + rng.uniform(-45, 45), lo), hi)
        solution = kin.inverse(target, seed)
        assert math.dist(kin.forward(solution), target) < 0.0011
        assert set(solution) == set(JOINTS)
        for n in SOLVED:
            lo, hi = URDF_LIMITS_DEG[n]
            assert lo - 1e-6 <= solution[n] <= hi + 1e-6
        assert solution["wrist_roll"] == seed["wrist_roll"] and solution["gripper"] == seed["gripper"]


def test_the_fold_in_target_at_the_base_origin_is_reachable():
    seed = {n: 0.0 for n in JOINTS}
    assert math.dist(kin.forward(kin.inverse((0.0, 0.0, 0.0), seed)), (0.0, 0.0, 0.0)) < 0.0011


def test_unreachable_or_malformed_targets_raise():
    seed = {n: 0.0 for n in JOINTS}
    for target in ((1.5, 0.0, 0.2), (0.0, 0.0, 5.0), (0.0, 0.7, 0.0)):
        with pytest.raises(ValueError):
            kin.inverse(target, seed)
    for target in ((float("nan"), 0, 0.1), (0.1, 0.1)):
        with pytest.raises(ValueError):
            kin.inverse(target, seed)
    with pytest.raises(ValueError, match="missing"):
        kin.forward({"shoulder_pan": 0.0})


def test_narrower_limits_are_respected_and_never_widened():
    narrow = SO101Kinematics({"shoulder_pan": (-5.0, 5.0), "wrist_flex": (-500.0, 500.0)})
    assert narrow.limits_deg["shoulder_pan"] == (-5.0, 5.0)
    assert narrow.limits_deg["wrist_flex"] == URDF_LIMITS_DEG["wrist_flex"]       # cannot widen past the URDF
    seed = {n: 0.0 for n in JOINTS}
    sideways = kin.forward({**seed, "shoulder_pan": 40.0, "shoulder_lift": -20.0, "elbow_flex": 20.0})
    with pytest.raises(ValueError):
        narrow.inverse(sideways, seed)                                              # needs a 40 degree pan
    with pytest.raises(ValueError, match="empty"):
        SO101Kinematics({"elbow_flex": (10.0, 5.0)})


def test_pan_positive_turns_the_arm_clockwise_seen_from_above():
    seed = {"shoulder_lift": -20.0, "elbow_flex": 20.0, "wrist_flex": 0.0, "wrist_roll": 0.0, "gripper": 0.0}
    left = kin.forward({**seed, "shoulder_pan": -30.0})
    right = kin.forward({**seed, "shoulder_pan": 30.0})
    assert left[1] > 0 > right[1]                                                    # as the real arm's Rest pose shows


def test_the_real_rest_pose_puts_the_tip_near_the_table_just_in_front_of_the_base():
    """Liam's Rest photo: gripper closed, tip just above the table. Measured joints (degrees) from several sessions.

    This is a plausibility check on the sign and zero conventions, NOT a calibration: the tip comes out about 1.4 cm below the
    base plane, so the true zero offsets are probably a few degrees different from the URDF's (3 degrees on the shoulder lift
    is 1.3 cm at that reach). A tip-touch test on the real arm is the proper check."""
    rest = {"shoulder_pan": -33.0, "shoulder_lift": -103.0, "elbow_flex": 98.0, "wrist_flex": 74.0, "wrist_roll": -77.0}
    x, y, z = kin.forward(rest)
    assert -0.03 < z < 0.06
    assert 0.0 < math.hypot(x, y) < 0.25 and x > 0


def test_link_positions_run_from_the_base_out_to_the_tip():
    q = {"shoulder_pan": 10.0, "shoulder_lift": -30.0, "elbow_flex": 40.0, "wrist_flex": 20.0, "wrist_roll": 0.0}
    points = kin.link_positions(q)
    assert len(points) == len(CHAIN) + 1 and points[-1] == pytest.approx(kin.forward(q))


def test_base_frame_round_trips_and_defaults_to_the_simulators_offset():
    frame = BaseFrame(offset=(0.05, -0.2, 0.0), yaw_deg=30.0)
    p = (0.1, 0.2, 0.03)
    assert frame.to_base(frame.to_marker(p)) == pytest.approx(p)
    assert BaseFrame().to_marker((0.2, 0.1, 0.05)) == pytest.approx((0.2, -0.02, 0.05))
    assert BaseFrame(yaw_deg=90.0).to_marker((1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0 - 0.12, 0.0))


def test_offsets_and_tool_shift_apply_to_readings_in_both_directions():
    offsets = {"shoulder_lift": -3.0, "elbow_flex": -6.6, "wrist_flex": -3.6}
    calibrated = SO101Kinematics(offsets_deg=offsets, tool_shift_m=-0.005)
    reading = {"shoulder_pan": 10.0, "shoulder_lift": -30.0, "elbow_flex": 40.0, "wrist_flex": 20.0, "wrist_roll": 5.0, "gripper": 3.0}
    shifted = {**reading, **{n: reading[n] + offsets[n] for n in offsets}}
    assert calibrated.forward(reading) == pytest.approx(kin.forward(shifted, tool_shift=-0.005))
    target = calibrated.forward(reading)
    seed = {**reading, "elbow_flex": 55.0, "shoulder_lift": -15.0}
    solution = calibrated.inverse(target, seed)                       # returns READINGS, not URDF degrees
    assert math.dist(calibrated.forward(solution), target) < 0.0011
    assert solution["wrist_roll"] == seed["wrist_roll"] and solution["gripper"] == seed["gripper"]


def test_no_calibration_is_the_plain_urdf_model():
    reading = {"shoulder_pan": 0.0, "shoulder_lift": -20.0, "elbow_flex": 30.0, "wrist_flex": 10.0, "wrist_roll": 0.0}
    assert SO101Kinematics().forward(reading) == pytest.approx(kin.forward(reading)) and SO101Kinematics().offsets_deg == dict.fromkeys(
        ("shoulder_lift", "elbow_flex", "wrist_flex"), 0.0)


CALIBRATION = ROOT / "calibration" / "so101-touch-2026-11-03.json"
TOUCHES = ROOT / "touches" / "2026-11-03.json"


@pytest.mark.skipif(not (CALIBRATION.exists() and TOUCHES.exists()), reason="the real tip-touch data is not in this checkout")
def test_the_saved_calibration_puts_the_real_touches_on_the_table_and_the_marker_where_it_is():
    """Regression on Liam's real touches (3 Nov): in-sample, so it checks the saved file and the maths, not generalisation
    (the held-out error, 2-3 mm in height, is in the file)."""
    import json
    calibrated = SO101Kinematics.from_calibration(str(CALIBRATION))
    frame = BaseFrame.from_calibration(str(CALIBRATION))
    points = json.loads(TOUCHES.read_text())["points"]
    heights = [calibrated.forward(p["joints"])[2] - (0.003 if p.get("marker_xy") else 0.0) for p in points]
    assert max(abs(h) for h in heights) < 0.004                                        # every touch within 4 mm of its plane
    for p in points:
        if p.get("marker_xy"):
            x, y, _ = frame.to_marker(calibrated.forward(p["joints"]))
            assert math.hypot(x - p["marker_xy"][0], y - p["marker_xy"][1]) < 0.012     # marker touches within 12 mm in xy
    held_out = json.loads(CALIBRATION.read_text())["held_out"]["heights, 4 parameters"]
    assert held_out["median_mm"] < 4.0
