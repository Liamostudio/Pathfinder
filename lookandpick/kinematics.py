"""SO-101 forward and inverse kinematics from the vendored URDF, in pure Python (no dependencies).

One module for the simulator's twin and the real arm, so "the real arm follows the twin's plan" is literally the same
maths. It matches the `Kinematics` protocol in `adapters/so101.py`:

    forward(joints) -> (x, y, z)            joints in DEGREES, the way LeRobot reports them (gripper ignored)
    inverse(target, seed) -> joints         all six joints in degrees; wrist_roll and gripper are carried over from `seed`

Frames and conventions (read before trusting a number):
  * Positions are metres in the ARM-BASE frame: the URDF `base_link`, z up. The simulator places `base_link` on the table
    plane, so z is the height above the table. Converting to the ArUco marker frame is `BaseFrame`, whose offset and yaw
    MUST be measured on the real build (the offset placeholder matches the simulator's ARM_BASE_OFFSET).
  * The point is the URDF `gripper_frame_link`: the same `tcp` site the simulator's IK targets (between the jaws).
  * Joint degrees equal the URDF angle in degrees. That works because LeRobot's degrees are centred on the calibration
    midpoint and the vendored `so101_new_calib.urdf` is the new-calibration model. `tests/test_kinematics.py` checks it
    against the real arm's measured Rest pose and, in an environment with MuJoCo, against the simulator's own FK.
  * IK is position-only (three equations, four joints: pan, lift, elbow, wrist flex), damped least squares from the seed,
    so the answer is the nearby one and the wrist pitch is whatever the seed's nullspace gives. wrist_roll barely moves
    the TCP (8 mm off-axis) and is kept as the seed's. It checks joint limits only: NO collision, table or hand checks;
    those belong to the adapter's workspace box and the safety layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

Vec3 = tuple[float, float, float]

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
CHAIN = JOINTS[:5]
SOLVED = JOINTS[:4]

# (name, origin xyz, origin rpy, (lower, upper) radians): every joint rotates about its own +z.
# Copied from simulation/assets/so101/so101_new_calib.urdf; tests/test_kinematics.py re-parses that file and compares.
_URDF_CHAIN = (
    ("shoulder_pan", (0.0388353, -8.97657e-09, 0.0624), (3.14159, 4.18253e-17, -3.14159), (-1.91986, 1.91986)),
    ("shoulder_lift", (-0.0303992, -0.0182778, -0.0542), (-1.5708, -1.5708, 0.0), (-1.74533, 1.74533)),
    ("elbow_flex", (-0.11257, -0.028, 1.73763e-16), (-3.63608e-16, 8.74301e-16, 1.5708), (-1.69, 1.69)),
    ("wrist_flex", (-0.1349, 0.0052, 3.62355e-17), (4.02456e-15, 8.67362e-16, -1.5708), (-1.65806, 1.65806)),
    ("wrist_roll", (5.55112e-17, -0.0611, 0.0181), (1.5708, 0.0486795, 3.14159), (-2.74385, 2.84121)),
)
_URDF_TCP = ((-0.0079, -0.000218121, -0.0981274), (0.0, 3.14159, 0.0))

URDF_LIMITS_DEG = {name: (math.degrees(lo), math.degrees(hi)) for name, _, _, (lo, hi) in _URDF_CHAIN}

Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def _rpy(roll: float, pitch: float, yaw: float) -> Matrix:
    """URDF fixed-axis rpy: R = Rz(yaw) Ry(pitch) Rx(roll)."""
    cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
            (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
            (-sp, cp * sr, cp * cr))


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _apply(r: Matrix, v: Vec3) -> Vec3:
    return tuple(sum(r[i][k] * v[k] for k in range(3)) for i in range(3))


def _rz(angle: float) -> Matrix:
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _frames(angles: dict[str, float]):
    """Per chain joint: (origin, z axis) in the base frame, plus the TCP position. Angles in radians."""
    rotation: Matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    position: Vec3 = (0.0, 0.0, 0.0)
    joints = []
    for name, xyz, rpy, _ in _URDF_CHAIN:
        offset = _apply(rotation, xyz)
        position = (position[0] + offset[0], position[1] + offset[1], position[2] + offset[2])
        rotation = _mul(_mul(rotation, _rpy(*rpy)), _rz(angles[name]))
        joints.append((position, (rotation[0][2], rotation[1][2], rotation[2][2])))
    offset = _apply(rotation, _URDF_TCP[0])
    tcp = (position[0] + offset[0], position[1] + offset[1], position[2] + offset[2])
    return joints, tcp


def _radians(joints_deg: dict[str, float]) -> dict[str, float]:
    missing = [n for n in CHAIN if n not in joints_deg]
    if missing:
        raise ValueError(f"joints missing: {missing}")
    return {n: math.radians(joints_deg[n]) for n in CHAIN}


def _solve3(m: list[list[float]], b: list[float]) -> list[float]:
    """Solve a 3x3 system by Cramer's rule (the damped normal matrix is always invertible)."""
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    d = det(m)
    out = []
    for col in range(3):
        t = [row[:] for row in m]
        for r in range(3):
            t[r][col] = b[r]
        out.append(det(t) / d)
    return out


class SO101Kinematics:
    def __init__(self, limits_deg: dict[str, tuple[float, float]] | None = None, *, tolerance_m: float = 0.001,
                 max_iterations: int = 300):
        """`limits_deg` narrows the URDF limits (never widens them); defaults to the URDF's own."""
        merged = dict(URDF_LIMITS_DEG)
        for name, (lo, hi) in (limits_deg or {}).items():
            if name in merged:
                merged[name] = (max(lo, merged[name][0]), min(hi, merged[name][1]))
        for name in SOLVED:
            if not merged[name][0] < merged[name][1]:
                raise ValueError(f"empty joint range for {name}")
        self.limits_deg = merged
        self.tolerance_m, self.max_iterations = tolerance_m, max_iterations

    # -- forward -----------------------------------------------------------------------------------------------------
    def forward(self, joints: dict[str, float]) -> Vec3:
        return _frames(_radians(joints))[1]

    def link_positions(self, joints: dict[str, float]) -> list[Vec3]:
        """Origins of the five joints and then the TCP, base frame: the arm's skeleton, for hand-distance checks."""
        frames, tcp = _frames(_radians(joints))
        return [origin for origin, _ in frames] + [tcp]

    # -- inverse -----------------------------------------------------------------------------------------------------
    def inverse(self, target: Vec3, seed: dict[str, float]) -> dict[str, float]:
        """Joints (degrees) that put the TCP within tolerance of `target`; ValueError if none is reachable in the limits."""
        if len(target) != 3 or not all(math.isfinite(v) for v in target):
            raise ValueError("target must be three finite metres")
        base = _radians(seed)
        limits = {n: tuple(math.radians(v) for v in self.limits_deg[n]) for n in SOLVED}
        for start in self._starts(base, target, limits):
            solution = self._descend(start, base["wrist_roll"], target, limits)
            if solution is not None:
                out = {n: math.degrees(solution[n]) for n in SOLVED}
                out["wrist_roll"] = seed["wrist_roll"]
                out["gripper"] = seed.get("gripper", 0.0)
                return out
        raise ValueError("target not reachable within the joint limits")

    def _starts(self, base, target, limits):
        yield {n: min(max(base[n], limits[n][0]), limits[n][1]) for n in SOLVED}
        # Fallbacks (the simulator's seeds, radians) with the pan turned to face the target.
        facing = self._pan_towards(target, limits)
        for lift, elbow, wrist in ((-0.5, 1.0, 0.6), (0.3, -0.5, -0.8), (-0.8, 1.1, 0.6), (0.0, 0.0, 0.0)):
            candidate = {"shoulder_pan": facing, "shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": wrist}
            yield {n: min(max(candidate[n], limits[n][0]), limits[n][1]) for n in SOLVED}

    def _pan_towards(self, target: Vec3, limits) -> float:
        """The pan angle that turns the arm's horizontal reach towards `target` (works whichever way pan turns)."""
        def azimuth(pan):
            angles = {n: 0.0 for n in CHAIN}
            angles.update(shoulder_lift=-0.5, elbow_flex=1.0, wrist_flex=0.6, shoulder_pan=pan)
            x, y, _ = _frames(angles)[1]
            return math.atan2(y, x)
        a0, a1 = azimuth(0.0), azimuth(0.5)
        slope = math.atan2(math.sin(a1 - a0), math.cos(a1 - a0)) / 0.5
        wanted = math.atan2(target[1], target[0])
        pan = math.atan2(math.sin(wanted - a0), math.cos(wanted - a0)) / slope
        return min(max(pan, limits["shoulder_pan"][0]), limits["shoulder_pan"][1])

    def _descend(self, q, roll, target, limits):
        q = dict(q)
        for _ in range(self.max_iterations):
            angles = dict(q, wrist_roll=roll)
            frames, tcp = _frames(angles)
            error = [target[i] - tcp[i] for i in range(3)]
            if math.sqrt(sum(e * e for e in error)) < self.tolerance_m:
                return q
            # Position Jacobian of a revolute joint: z axis x (tcp - joint origin).
            columns = []
            for name in SOLVED:
                origin, axis = frames[CHAIN.index(name)]
                arm = (tcp[0] - origin[0], tcp[1] - origin[1], tcp[2] - origin[2])
                columns.append((axis[1] * arm[2] - axis[2] * arm[1], axis[2] * arm[0] - axis[0] * arm[2],
                                axis[0] * arm[1] - axis[1] * arm[0]))
            damping = 0.02 ** 2
            m = [[sum(c[i] * c[j] for c in columns) + (damping if i == j else 0.0) for j in range(3)] for i in range(3)]
            y = _solve3(m, error)
            for k, name in enumerate(SOLVED):
                step = sum(columns[k][i] * y[i] for i in range(3))
                step = max(-0.15, min(0.15, step))
                q[name] = min(max(q[name] + step, limits[name][0]), limits[name][1])
        return None


@dataclass(frozen=True)
class BaseFrame:
    """Arm-base frame <-> ArUco marker frame (origin at the marker centre, z up), for a base on the table plane.

    `offset` is where the pan axis stands in the marker frame, `yaw_deg` how far the arm's base x axis is turned from the
    marker's +x (counter-clockwise from above). Both are placeholders until measured on the real build: the offset is the
    simulator's ARM_BASE_OFFSET and the yaw 0 (which is also what the simulator assumes).
    """
    offset: Vec3 = (0.0, -0.12, 0.0)
    yaw_deg: float = 0.0

    def to_marker(self, p: Vec3) -> Vec3:
        c, s = math.cos(math.radians(self.yaw_deg)), math.sin(math.radians(self.yaw_deg))
        return (self.offset[0] + c * p[0] - s * p[1], self.offset[1] + s * p[0] + c * p[1], self.offset[2] + p[2])

    def to_base(self, p: Vec3) -> Vec3:
        c, s = math.cos(math.radians(self.yaw_deg)), math.sin(math.radians(self.yaw_deg))
        x, y = p[0] - self.offset[0], p[1] - self.offset[1]
        return (c * x + s * y, -s * x + c * y, p[2] - self.offset[2])
