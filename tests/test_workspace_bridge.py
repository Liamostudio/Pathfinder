"""The arm calibration -> workspace bridge, checked against the workspace's own robot_transform convention."""
import json
import math

import pytest

from lookandpick.kinematics import BaseFrame, SO101Kinematics
from lookandpick.workspace_bridge import marker_from_base, robot_placement, urdf_joint_radians
from lookandpick.workspaces.schema import robot_transform

TOUCH = "calibration/so101-touch-2026-11-03.json"


def rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def canonical_from_marker(scale=3.2, yaw=25.0, tilt=0.0, origin=(0.4, -0.2, 0.7)):
    r = rz(yaw)
    return [[r[i][j] * scale for j in range(3)] + [origin[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def apply(m, p):
    return [sum(m[i][j] * p[j] for j in range(3)) + m[i][3] for i in range(3)]


@pytest.mark.parametrize("yaw", [0.0, 138.1, -90.0, 179.0])
def test_the_editors_own_robot_transform_reproduces_marker_from_base_through_the_scene(yaw):
    frame = BaseFrame((0.217, -0.031, 0.0), yaw)
    cfm = canonical_from_marker(scale=3.2, yaw=25.0)
    placement = robot_placement(cfm, frame)
    editor = robot_transform(placement)                       # the schema's convention, not mine
    expected = [[sum(cfm[i][k] * marker_from_base(frame)[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    for i in range(4):
        for j in range(4):
            assert editor[i][j] == pytest.approx(expected[i][j], abs=1e-9)
    assert placement["scale"] == pytest.approx(3.2) and placement["placement_status"] == "touch_calibrated"


def test_the_arm_base_lands_at_the_measured_offset_in_the_marker_frame():
    frame = BaseFrame((0.217, -0.031, 0.0), 138.1)
    cfm = canonical_from_marker()
    editor = robot_transform(robot_placement(cfm, frame))
    # the robot's base origin, taken into the scene, must equal the marker offset taken into the scene
    assert apply(editor, [0, 0, 0]) == pytest.approx(apply(cfm, [0.217, -0.031, 0.0]), abs=1e-9)
    # and a point 0.2 m ahead of the base (its +x) must land at the marker-frame point the BaseFrame says
    ahead = apply(editor, [0.2, 0.0, 0.0])
    assert ahead == pytest.approx(apply(cfm, frame.to_marker((0.2, 0.0, 0.0))), abs=1e-9)


def test_the_kinematics_tip_and_the_placed_robot_agree_on_where_the_tip_is():
    """Rest tip from the calibrated FK, through BaseFrame and the scene, equals the same point drawn through the placed robot."""
    kin = SO101Kinematics.from_calibration(TOUCH)
    frame = BaseFrame.from_calibration(TOUCH)
    cfm = canonical_from_marker(scale=2.5, yaw=-40.0)
    tip_base = kin.forward({"shoulder_pan": -33.4, "shoulder_lift": -103.1, "elbow_flex": 98.0, "wrist_flex": 73.1, "wrist_roll": -77.0})
    via_marker = apply(cfm, frame.to_marker(tip_base))
    via_robot = apply(robot_transform(robot_placement(cfm, frame)), tip_base)
    assert via_robot == pytest.approx(via_marker, abs=1e-9)


def test_an_unusable_marker_transform_is_refused():
    with pytest.raises(ValueError):
        robot_placement([[0.0] * 4, [0.0] * 4, [0.0] * 4, [0, 0, 0, 1.0]], BaseFrame())


def test_readings_become_urdf_radians_with_the_calibrated_offsets():
    offsets = json.loads(open(TOUCH).read())["offsets_deg"]
    rest = {"shoulder_pan": -33.4, "shoulder_lift": -103.1, "elbow_flex": 98.0, "wrist_flex": 73.1, "wrist_roll": -77.0, "gripper": 1.3}
    out = urdf_joint_radians(rest, offsets)
    assert out["shoulder_pan"] == pytest.approx(math.radians(-33.4)) and out["wrist_roll"] == pytest.approx(math.radians(-77.0))
    assert out["shoulder_lift"] == pytest.approx(math.radians(-103.1 + offsets["shoulder_lift"]))
    assert -0.1745 < out["gripper"] < 0.0                                    # nearly closed maps near the URDF's closed end
    assert urdf_joint_radians({"gripper": 100.0}, {})["gripper"] == pytest.approx(1.74533)
