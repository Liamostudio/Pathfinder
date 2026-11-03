"""The commissioned SO-101 profile: Liam's real calibration turned into limits, kinematics and a marker-frame arm.

Built from measurements (3 Nov), nothing here is a guess about the hardware:
  * joint ranges come from the LeRobot calibration (`calibration/so101-follower-lerobot-*.json`), shrunk by a margin;
  * the tip kinematics and the arm-base frame come from the tip-touch calibration (`calibration/so101-touch-*.json`);
  * the profile refuses to build unless both files are there and the touch fit's held-out error is small.

This only BUILDS the objects. Nothing here connects to, powers or moves the arm; a live move still needs
`SO101Arm(dry_run=False, i_am_present=True)` with a connected bus and Liam at the terminal with the 5V kill switch in reach,
and a safe start (goals set to the present pose before torque, see scripts/so101_nudge.py `arm_hold`), which this module does
not provide.

Frames: `SO101Arm` works in the ARM-BASE frame (its workspace box and minimum reach are measured from the pan axis).
`MarkerFrameArm` wraps it for the rest of the system, which works in the ArUco-marker (world) frame, using the measured
`BaseFrame`.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..kinematics import BaseFrame, SO101Kinematics
from ..world import Vec3
from .so101 import JOINTS, SO101Arm, SO101Limits

CALIBRATION_DIR = Path(__file__).resolve().parents[2] / "calibration"
LEROBOT_CALIBRATION = CALIBRATION_DIR / "so101-follower-lerobot-2026-11-03.json"
TOUCH_CALIBRATION = CALIBRATION_DIR / "so101-touch-2026-11-03.json"

RANGE_MARGIN_DEG = 3.0            # targets stay this far inside the calibrated range
WRIST_ROLL_RANGE = (-170.0, 170.0)  # a full-turn joint; stay off the cable limit
GRIPPER_RANGE = (0.0, 50.0)       # LeRobot's 0..100 scale: half open at most for now
MAX_HELD_OUT_MEDIAN_MM = 5.0      # the touch calibration must predict unseen touches at least this well
# Base-frame envelope (metres, arm-base frame): a sanity bound, NOT a collision check. Rest and every touched point lie inside.
WORKSPACE_MIN = (-0.12, -0.34, 0.01)   # 1 cm above the table: the calibration is good to about 3 mm
WORKSPACE_MAX = (0.34, 0.34, 0.30)
MIN_REACH_M = 0.08                # nothing within 8 cm of the pan axis: the base and the arm's own links are there
CARTESIAN_STEP_M = 0.01           # straight-line moves in 1 cm waypoints
COMPLETION_TOLERANCE_DEG = 0.3    # about three motor ticks: the real arm never lands on a goal to 0.0001 degrees

# A typical Rest reading (LeRobot degrees; the arm is placed by hand, so a few degrees vary). Rest sits on the calibrated ends
# of shoulder_lift and elbow_flex, which is why observed joints get a margin beyond the target range.
REST_READING = {"shoulder_pan": -33.4, "shoulder_lift": -103.1, "elbow_flex": 98.0, "wrist_flex": 73.1,
                "wrist_roll": -77.0, "gripper": 1.3}


def lerobot_ranges_deg(path: Path = LEROBOT_CALIBRATION) -> dict[str, tuple[float, float]]:
    """Half-spans of the calibrated joints in the degrees LeRobot reports (centred on the calibration midpoint)."""
    data = json.loads(Path(path).read_text())
    ranges = {}
    for name in JOINTS[:5]:
        if name not in data:
            raise ValueError(f"calibration has no {name}")
        half = (data[name]["range_max"] - data[name]["range_min"]) / 2 * 360 / 4095
        ranges[name] = (-half, half)
    return ranges


def _check_touch_calibration(path: Path) -> dict:
    record = json.loads(Path(path).read_text())
    held = record.get("held_out", {}).get("heights, 4 parameters")
    if not held or held["median_mm"] > MAX_HELD_OUT_MEDIAN_MM:
        raise ValueError(f"the touch calibration is not validated (held-out median must be at most {MAX_HELD_OUT_MEDIAN_MM} mm)")
    if not record.get("base_frame"):
        raise ValueError("the touch calibration has no base frame (no marker touches)")
    return record


def commissioned_limits(lerobot_calibration: Path = LEROBOT_CALIBRATION, touch_calibration: Path = TOUCH_CALIBRATION,
                        margin_deg: float = RANGE_MARGIN_DEG) -> SO101Limits:
    """Limits from the measured ranges. Raises unless both calibration files exist and the touch fit is validated."""
    for path in (lerobot_calibration, touch_calibration):
        if not Path(path).exists():
            raise FileNotFoundError(f"cannot commission without {path}")
    _check_touch_calibration(touch_calibration)
    ranges = {name: (lo + margin_deg, hi - margin_deg) for name, (lo, hi) in lerobot_ranges_deg(lerobot_calibration).items()}
    ranges["wrist_roll"] = WRIST_ROLL_RANGE
    ranges["gripper"] = GRIPPER_RANGE
    return SO101Limits(joint_ranges=ranges, workspace_min=WORKSPACE_MIN, workspace_max=WORKSPACE_MAX, max_velocity=10.0,
                       max_step=0.5, commissioned=True, observation_margin_deg=2 * margin_deg, min_reach_m=MIN_REACH_M,
                       cartesian_step_m=CARTESIAN_STEP_M, completion_tolerance_deg=COMPLETION_TOLERANCE_DEG)


def calibrated_kinematics(touch_calibration: Path = TOUCH_CALIBRATION, limits: SO101Limits | None = None) -> SO101Kinematics:
    """Calibrated FK/IK. With `limits`, the IK searches only inside the profile's joint ranges (shifted into URDF degrees)."""
    record = _check_touch_calibration(touch_calibration)
    offsets = record["offsets_deg"]
    narrowed = None
    if limits is not None:
        narrowed = {n: (lo + offsets.get(n, 0.0), hi + offsets.get(n, 0.0)) for n, (lo, hi) in limits.joint_ranges.items() if n in JOINTS[:4]}
    return SO101Kinematics(limits_deg=narrowed, offsets_deg=offsets, tool_shift_m=record["tool_shift_m"])


class MarkerFrameArm:
    """An arm that speaks the marker (world) frame while the wrapped arm works in its own base frame."""

    def __init__(self, inner, frame: BaseFrame):
        self.inner, self.frame = inner, frame

    @property
    def position(self) -> Vec3:
        return self.frame.to_marker(self.inner.position)

    @property
    def joints(self) -> dict[str, float]:
        return self.inner.joints

    def move_to(self, target: Vec3, speed_scale: float) -> bool:
        if len(target) != 3 or not all(math.isfinite(v) for v in target):
            return False
        return self.inner.move_to(self.frame.to_base(target), speed_scale)

    def grip(self, closed: bool) -> bool:
        return self.inner.grip(closed)

    def halt(self) -> None:
        self.inner.halt()

    def set_speed_scale(self, scale: float) -> None:
        self.inner.set_speed_scale(scale)


def dry_run_arm(initial: dict[str, float] | None = None, *, touch_calibration: Path = TOUCH_CALIBRATION,
                lerobot_calibration: Path = LEROBOT_CALIBRATION) -> tuple[MarkerFrameArm, SO101Arm]:
    """The real profile with no hardware: plans and integrates joint steps in software only. Returns (marker-frame arm, inner arm)."""
    limits = commissioned_limits(lerobot_calibration, touch_calibration)
    frame = BaseFrame.from_calibration(str(touch_calibration))
    inner = SO101Arm(kinematics=calibrated_kinematics(touch_calibration, limits), limits=limits, dry_run=True,
                     initial_joints=dict(initial or REST_READING))
    return MarkerFrameArm(inner, frame), inner
