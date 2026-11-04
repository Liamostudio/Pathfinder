"""Bridge between the measured arm calibration and the workspace reconstruction (Codex's `lookandpick/workspaces`).

The workspace editor places the SO-101 model by hand ("illustrative"). With the tip-touch calibration we know where the arm base
really is in the ArUco marker frame (`BaseFrame`), and the workspace already stores where the marker is in the scene
(`canonical_from_marker`, from `marker_alignment.estimate`, which uses the same marker frame as `calibration.py`: origin at the
square's centre, +X right, +Y up as in docs/marker-axes.png, z up). Composing the two places the robot model where the real arm
stands, and the same joint offsets turn real readings into the URDF angles the editor draws.

Pure Python, no dependencies, no hardware, no I/O. Wiring these into the editor is the workspace owner's job; this module is the
checked arithmetic. `tests/test_workspace_bridge.py` verifies it against `workspaces.schema.robot_transform`, the convention the
editor and the exports actually use.
"""
from __future__ import annotations

import math

from .kinematics import OFFSET_JOINTS, BaseFrame

Matrix4 = list[list[float]]

# URDF gripper limits (radians) from so101_new_calib.urdf; the LeRobot gripper reading is 0..100 across the calibrated span.
URDF_GRIPPER_RAD = (-0.174533, 1.74533)


def marker_from_base(frame: BaseFrame) -> Matrix4:
    """The arm-base frame expressed in the marker frame, metres: a yaw about z and the measured offset."""
    c, s = math.cos(math.radians(frame.yaw_deg)), math.sin(math.radians(frame.yaw_deg))
    return [[c, -s, 0.0, frame.offset[0]], [s, c, 0.0, frame.offset[1]], [0.0, 0.0, 1.0, frame.offset[2]], [0.0, 0.0, 0.0, 1.0]]


def _mul(a: Matrix4, b: Matrix4) -> Matrix4:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _euler_xyz_degrees(rotation: list[list[float]]) -> list[float]:
    """Roll, pitch, yaw (degrees) for R = Rz(yaw) Ry(pitch) Rx(roll): the convention of `workspaces.schema.robot_transform`."""
    pitch = -math.asin(max(-1.0, min(1.0, rotation[2][0])))
    if abs(math.cos(pitch)) < 1e-9:                       # gimbal lock: fold the roll into the yaw
        return [0.0, math.degrees(pitch), math.degrees(math.atan2(-rotation[0][1], rotation[1][1]))]
    return [math.degrees(math.atan2(rotation[2][1], rotation[2][2])), math.degrees(pitch),
            math.degrees(math.atan2(rotation[1][0], rotation[0][0]))]


def robot_placement(canonical_from_marker: Matrix4, frame: BaseFrame) -> dict:
    """The editor's `robot` placement fields (translation, rotation in degrees, scale) for the measured base frame.

    `canonical_from_marker` is the workspace's marker pose in the scene; its rotation block carries the scene's units per metre,
    which becomes the robot's scale (the URDF is in metres), exactly as `workspaces.compose` derives it from a robot report."""
    m = _mul(canonical_from_marker, marker_from_base(frame))
    scale = math.sqrt(sum(m[i][0] ** 2 for i in range(3)))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("the marker transform has no usable scale")
    rotation = [[m[i][j] / scale for j in range(3)] for i in range(3)]
    return {"translation": [m[0][3], m[1][3], m[2][3]], "rotation": _euler_xyz_degrees(rotation), "scale": scale,
            "placement_status": "touch_calibrated", "placement_source": "so101 tip-touch calibration (base frame)"}


def urdf_joint_radians(reading_deg: dict[str, float], offsets_deg: dict[str, float], *, pan_draw_offset_deg: float = 0.0) -> dict[str, float]:
    """LeRobot readings -> the URDF joint angles (radians) the editor draws; the gripper is mapped linearly and only approximately."""
    out = {}
    for name in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"):
        if name in reading_deg:
            out[name] = math.radians(reading_deg[name] + (offsets_deg.get(name, 0.0) if name in OFFSET_JOINTS else 0.0))
    if "gripper" in reading_deg:
        low, high = URDF_GRIPPER_RAD
        out["gripper"] = low + (high - low) * max(0.0, min(100.0, reading_deg["gripper"])) / 100.0
    if 'shoulder_pan' in out:out['shoulder_pan'] += math.radians(pan_draw_offset_deg)
    return out
