"""SO-101 adapter scaffolding. Dry-run by default; no automatic port discovery/connect.

Limits below are conservative development settings, NOT measured hardware limits. Live
mode requires a commissioned profile, supplied FK/IK, a caller-owned connected follower,
and explicit interactive local presence. Nothing in this module is connected on import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import sys
import threading
import time
from typing import Protocol
from types import MappingProxyType

from ..safety import valid_position
from ..world import Vec3

JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
DEVELOPMENT_RANGES = MappingProxyType({
    'shoulder_pan': (-60.,60.), 'shoulder_lift': (-45.,45.), 'elbow_flex': (-60.,60.),
    'wrist_flex': (-45.,45.), 'wrist_roll': (-60.,60.), 'gripper': (0.,20.),
})


class Kinematics(Protocol):
    def inverse(self, target: Vec3, seed: dict[str, float]) -> dict[str, float]: ...
    def forward(self, joints: dict[str, float]) -> Vec3: ...


class ArmBus(Protocol):
    def read_positions(self) -> dict[str, float]: ...
    def send_positions(self, joints: dict[str, float]) -> None: ...
    def halt(self) -> None: ...


@dataclass(frozen=True)
class SO101Limits:
    # Body joints are degrees; gripper is LeRobot's calibrated 0..100 scale.
    joint_ranges: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEVELOPMENT_RANGES))
    max_velocity: float = 15.0  # degrees/s for body; normalised units/s for gripper
    max_step: float = 1.0
    period_s: float = 1 / 30
    workspace_min: Vec3 = (-.30, -.30, .0)
    workspace_max: Vec3 = (.30, .30, .40)
    gripper_torque_raw: int = 100  # raw torque limit, NOT a force in newtons
    commissioned: bool = False

    def __post_init__(self):
        if set(self.joint_ranges) != set(JOINTS):
            raise ValueError('All six joint limits are required')
        for name, (low, high) in self.joint_ranges.items():
            if not math.isfinite(low) or not math.isfinite(high) or low >= high or low < DEVELOPMENT_RANGES[name][0] or high > DEVELOPMENT_RANGES[name][1]:
                raise ValueError('Invalid joint limits')
        if not 0 < self.max_velocity <= 15 or not 0 < self.max_step <= 1 or not 0 < self.period_s <= .1:
            raise ValueError('Development velocity/step ceilings cannot be exceeded')
        if not isinstance(self.gripper_torque_raw, int) or not 1 <= self.gripper_torque_raw <= 100:
            raise ValueError('Gripper torque ceiling is 100 raw units')
        if not valid_position(self.workspace_min) or not valid_position(self.workspace_max) or not all(a < b for a,b in zip(self.workspace_min,self.workspace_max)):
            raise ValueError('Invalid workspace')
        object.__setattr__(self, 'joint_ranges', MappingProxyType({k:tuple(v) for k,v in self.joint_ranges.items()}))
        object.__setattr__(self, 'workspace_min', tuple(self.workspace_min))
        object.__setattr__(self, 'workspace_max', tuple(self.workspace_max))

    def contains(self, position):
        return valid_position(position) and all(a <= p <= b for a,p,b in zip(self.workspace_min,position,self.workspace_max))


def require_local_presence(i_am_present=False, *, stdin=None, stdout=None, confirm=None, environ=None):
    stdin, stdout = (sys.stdin if stdin is None else stdin), (sys.stdout if stdout is None else stdout)
    environ = os.environ if environ is None else environ
    if not i_am_present or not stdin.isatty() or not stdout.isatty() or any(environ.get(k) for k in ('SSH_CONNECTION','SSH_CLIENT','SSH_TTY')):
        raise PermissionError('Live mode requires --i-am-present in a local interactive terminal')
    prompt = 'With the 6V kill switch in reach, type exactly: 6V KILL SWITCH READY\n> '
    answer = (input if confirm is None else confirm)(prompt)
    if answer != '6V KILL SWITCH READY':
        raise PermissionError('Physical-presence confirmation failed')


class LeRobotFollowerBus:
    """Wrap an ALREADY connected and calibrated follower; never call its connect/configure.

    LeRobot send_action reports a submitted target, so completion comes from observations.
    Constructing or connecting a real follower is deliberately outside this adapter.
    """

    def __init__(self, follower):
        if not follower.is_connected or not follower.is_calibrated or not follower.config.use_degrees:
            raise ValueError('Follower must already be connected, calibrated and using degrees')
        self.follower = follower

    def read_positions(self):
        # Read only the bus, never block the controller on follower camera acquisition.
        return {key: float(value) for key,value in self.follower.bus.sync_read('Present_Position').items()}

    def set_gripper_torque_limit(self, raw):
        self.follower.bus.write('Max_Torque_Limit', 'gripper', raw)

    def send_positions(self, joints):
        self.follower.send_action({f'{name}.pos': value for name,value in joints.items()})

    def halt(self):
        self.follower.bus.disable_torque()


def follower_class():
    """Lazy import only; creating/connecting hardware is never an import side effect."""
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    return SO101Follower, SO101FollowerConfig


class SO101Arm:
    def __init__(self, *, bus: ArmBus | None = None, kinematics: Kinematics | None = None,
                 limits: SO101Limits | None = None, dry_run=True, i_am_present=False,
                 initial_joints=None, presence_check=require_local_presence):
        self.limits = SO101Limits() if limits is None else limits
        self.dry_run = bool(dry_run)
        if not self.dry_run:
            presence_check(i_am_present)
            if not self.limits.commissioned or bus is None or kinematics is None:
                raise PermissionError('Live mode requires commissioned limits, FK/IK and an already connected bus')
            if not hasattr(bus, 'set_gripper_torque_limit'):
                raise PermissionError('Bus cannot enforce the gripper torque cap')
        self.bus, self.kinematics = bus, kinematics
        self._stop = threading.Event()
        self._action = threading.Lock()
        self._dispatch = threading.RLock()
        self._halt_sent = False
        self._scale = 1.0
        self.log = []
        self.joints = dict(initial_joints if initial_joints is not None else {name:0. for name in JOINTS})
        self._validate_observation(self.joints)
        self.position = (0.,0.,.2) if kinematics is None else tuple(kinematics.forward(self.joints))
        if not self.limits.contains(self.position):
            raise ValueError('Initial pose is outside the workspace')
        self.gripping = False
        if not self.dry_run:
            self.joints = self.bus.read_positions()
            self._validate_observation(self.joints)
            self.position = tuple(kinematics.forward(self.joints))
            if not self.limits.contains(self.position):
                raise ValueError('Observed pose is outside the workspace')
            self.bus.set_gripper_torque_limit(self.limits.gripper_torque_raw)

    def _validate_joints(self, joints):
        if set(joints) != set(JOINTS) or not all(math.isfinite(v) for v in joints.values()):
            raise ValueError('A finite value for every joint is required')

    def _validate_observation(self, joints):
        self._validate_joints(joints)
        if not all(self.limits.joint_ranges[name][0] <= value <= self.limits.joint_ranges[name][1] for name,value in joints.items()):
            raise ValueError('Observed joint is outside its limits')

    def preview_step(self, target, *, speed_scale=1.0):
        """Pure bounded plan preview; does not send or update the current pose."""
        self._validate_joints(target)
        if not math.isfinite(speed_scale) or not 0 <= speed_scale <= 1:
            raise ValueError('Invalid speed scale')
        step = min(self.limits.max_step, self.limits.max_velocity * self.limits.period_s) * min(speed_scale,self._scale)
        bounded = {}
        for name in JOINTS:
            low, high = self.limits.joint_ranges[name]
            value = min(high, max(low, target[name]))
            bounded[name] = self.joints[name] + max(-step, min(step, value-self.joints[name]))
        return bounded

    def set_speed_scale(self, scale):
        if not math.isfinite(scale) or not 0 <= scale <= 1:
            self.halt()
            raise ValueError('Invalid speed scale')
        self._scale = 0.0 if self._stop.is_set() else scale
        # Position servos keep moving toward their last submitted target. A zero scale
        # must therefore stop, not merely stop issuing new targets. Live pauses latch.
        if scale == 0 and not self.dry_run:
            self.halt()

    def halt(self):
        self._stop.set()  # bypass the action lock, including a long move
        self._scale = 0.0
        self.log.append(('halt', None))
        with self._dispatch:
            if not self.dry_run and not self._halt_sent:
                self._halt_sent = True
                self.bus.halt()

    def move_to(self, target: Vec3, speed_scale: float):
        if self._stop.is_set() or not self.limits.contains(target) or self.kinematics is None:
            self.log.append(('refused_cartesian', target))
            return False
        try:
            joints = self.kinematics.inverse(target, dict(self.joints))
            if joints.get('gripper') != self.joints['gripper']:
                raise ValueError('Cartesian planner must preserve gripper position')
            completed = self.move_joints(joints, speed_scale=speed_scale)
            return completed and math.dist(self.position, target) <= .002
        except Exception as exc:
            self.halt()
            self.log.append(('error', str(exc)))
            return False

    def move_joints(self, target, *, speed_scale=1.0, timeout_s=5.0):
        if self._stop.is_set() or self.kinematics is None or not self._action.acquire(blocking=False):
            return False
        try:
            self._validate_joints(target)
            if not math.isfinite(timeout_s) or not 0 < timeout_s <= 30:
                raise ValueError('Invalid move timeout')
            # Validate FK for the requested endpoint, not just each intermediate step.
            if not self.limits.contains(self.kinematics.forward(target)):
                return False
            bounded_target = {name:min(self.limits.joint_ranges[name][1],max(self.limits.joint_ranges[name][0],value))
                              for name,value in target.items()}
            deadline = time.monotonic() + timeout_s
            for _ in range(10000):
                if self._stop.is_set() or time.monotonic() >= deadline:
                    self.halt()
                    return False
                if self._scale == 0:
                    if self.dry_run:
                        return False
                    self._stop.wait(self.limits.period_s)
                    continue
                next_joints = self.preview_step(bounded_target, speed_scale=speed_scale)
                if next_joints == self.joints and next_joints != bounded_target:
                    return False
                position = tuple(self.kinematics.forward(next_joints))
                if not self.limits.contains(position):
                    self.halt()
                    return False
                with self._dispatch:
                    if self._stop.is_set():
                        return False
                    if not self.dry_run:
                        self.bus.send_positions(next_joints)
                    self.log.append(('dry_run' if self.dry_run else 'submitted', dict(next_joints)))
                if self.dry_run:
                    self.joints, self.position = next_joints, position
                else:
                    self._stop.wait(self.limits.period_s)
                    observed = self.bus.read_positions()
                    self._validate_observation(observed)
                    self.joints, self.position = observed, tuple(self.kinematics.forward(observed))
                    if not self.limits.contains(self.position):
                        self.halt()
                        return False
                if all(abs(self.joints[name]-bounded_target[name]) < 1e-4 for name in JOINTS):
                    return all(abs(bounded_target[name]-target[name]) < 1e-4 for name in JOINTS)
            self.halt()
            return False
        except Exception as exc:
            self.halt()
            self.log.append(('error', str(exc)))
            return False
        finally:
            self._action.release()

    def grip(self, closed):
        if self._stop.is_set() or self._scale <= 0:
            return False
        target = dict(self.joints)
        target['gripper'] = self.limits.joint_ranges['gripper'][0 if closed else 1]
        completed = self.move_joints(target)
        if completed:
            self.gripping = bool(closed)
        return completed
