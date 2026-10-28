"""Thread-safe simulation implementing Look-and-Pick's Arm and Tracker protocols.

Arm joints use velocity-limited kinematic servos; free objects use MuJoCo dynamics.
All commands complete synchronously, and stop can interrupt them from another thread.
"""
from __future__ import annotations
import math
import threading
import time
from dataclasses import replace
import mujoco
import numpy as np
from lookandpick.safety import Safety, point_segment_distance, valid_position
from lookandpick.tools import Tools
from lookandpick.world import Hand, TrackedObject, WorldState
from .model import JOINTS, OBJECTS, ARM_BASE_OFFSET, build_model


class Simulation:
    def __init__(self, *, realtime=True):
        self.model = build_model()
        self.data = mujoco.MjData(self.model)
        self.lock = threading.RLock()
        self.command_lock = threading.Lock()
        self.shutdown = threading.Event()
        self.safety = Safety()
        self.world = WorldState()
        self.tools = Tools(self.world, self, self, self.safety)
        self.jids = np.array([self.model.joint(n).id for n in JOINTS])
        self.qids = self.model.jnt_qposadr[self.jids]
        self.dofs = self.model.jnt_dofadr[self.jids]
        self.limits = self.model.jnt_range[self.jids].copy()
        self.tcp = self.model.site('tcp').id
        self.robot_bodies = [self.model.body(n).id for n in
                             ('shoulder_link', 'upper_arm_link', 'lower_arm_link', 'wrist_link', 'gripper_link', 'gripper_frame_link')]
        self.hands_list = []
        self.gripping = False
        self.held = None
        self.held_rotation = None
        self.halted = False
        self.speed_scale = 1.0
        self.effective_scale = 1.0
        self.target = None
        self._command_scale = 1.0
        self.reason = 'ready'
        self.events = []
        self.trace = []
        self.faults = set()
        self.seed = 0
        self.realtime = realtime
        self.reset()
        self.thread = None
        if realtime:
            self.thread = threading.Thread(target=self._run, name='sim-physics', daemon=True)
            self.thread.start()

    @property
    def position(self):
        with self.lock:
            return tuple(float(x) for x in self.data.site_xpos[self.tcp])

    def objects(self):
        with self.lock:
            return [TrackedObject(n, label, tuple(float(x) for x in self.data.body(n).xpos), self.held == n)
                    for n, (label, _, _) in OBJECTS.items()]

    def hands(self):
        with self.lock:
            return [replace(h) for h in self.hands_list]

    def event(self, message):
        self.events.append({'time': time.time(), 'message': str(message)})
        self.events = self.events[-60:]

    def reset(self):
        if not self.command_lock.acquire(blocking=False):
            raise ValueError('Wait for the active command to finish before resetting')
        try:
            with self.lock:
                mujoco.mj_resetData(self.model, self.data)
                self.data.qpos[self.qids] = [0, -0.35, 0.65, 0.35, 0, 0.8]
                self.data.qvel[:] = 0
                self.target = None
                self.held = None
                self.gripping = self.halted = False
                self.hands_list = []
                self.speed_scale = self.effective_scale = 1.0
                self.safety.reset()
                self.world = WorldState()
                self.tools.world = self.world
                self.reason = 'ready'
                self.trace = []
                self.faults.clear()
                self._hand_visible(False)
                mujoco.mj_forward(self.model, self.data)
                self.event('Scene reset · simulation only')
        finally:
            self.command_lock.release()

    def halt(self):
        # No command lock: this must interrupt a blocking move immediately.
        self.safety.stop()
        with self.lock:
            self.halted = True
            self.target = None
            self.speed_scale = self.effective_scale = 0
            self.reason = 'stop latched; reset required'
            self.event(self.reason)

    def set_speed_scale(self, scale):
        if not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0 <= scale <= 1:
            raise ValueError('Speed scale must be finite and in [0, 1]')
        with self.lock:
            self.speed_scale = 0 if self.halted else float(scale)

    def set_hand(self, position=None):
        if position is not None and (not valid_position(position) or max(abs(x) for x in position) > 2):
            raise ValueError('Hand must be a finite XYZ position within 2 m')
        with self.lock:
            self.hands_list = [] if position is None else [Hand('synthetic-hand', tuple(position), 0.045)]
            self._hand_visible(position is not None)
            if position is not None:
                self.data.mocap_pos[0] = position
            self.event('Hand cleared' if position is None else 'Synthetic hand moved')

    def _hand_visible(self, visible):
        for i in range(self.model.ngeom):
            if (self.model.geom(i).name or '').startswith('hand_'):
                self.model.geom_rgba[i,3] = .8 if visible else 0

    def _segments(self, data):
        points = [data.xpos[b] for b in self.robot_bodies] + [data.site_xpos[self.tcp]]
        return list(zip(points[:-1], points[1:]))

    def _safe_scale(self, data):
        # Conservative link-centre capsules, in addition to the existing TCP path gate.
        hands = [replace(h, radius_m=h.radius_m + 0.025) for h in self.hands_list]
        return min(self.safety.allowed_speed(hands, (tuple(a), tuple(b))) for a, b in self._segments(data))

    def _collision(self, data):
        table = self.model.geom('table').id
        base = self.model.body('base_link').id
        fingers = {self.model.body(n).id for n in ('gripper_link', 'moving_jaw_so101_v1_link')}
        objects = {self.model.body(n).id for n in OBJECTS}
        robot = set(self.robot_bodies) | fingers | {base}
        for c in data.contact:
            if c.dist > -0.001:
                continue
            a, b = map(int, self.model.geom_bodyid[[c.geom1, c.geom2]])
            # Table/base mounting contact and finger/object contact are expected.
            if ((c.geom1 == table and b in robot - {base}) or
                    (c.geom2 == table and a in robot - {base})):
                return 'arm intersects table'
            if ((a in robot - fingers and b in objects) or (b in robot - fingers and a in objects)):
                return 'arm link intersects object'
            if (a in robot and b in robot and a != b
                    and self.model.body_parentid[a] != b and self.model.body_parentid[b] != a):
                return 'arm self-collision'
        return None

    def step(self):
        with self.lock:
            q = self.data.qpos[self.qids].copy()
            scale = min(self.speed_scale, self._command_scale, self._safe_scale(self.data))
            self.effective_scale = scale
            candidate = q.copy()
            if 'serial_dropout' in self.faults:
                self.halt()
            if 'arm_stall' in self.faults:
                scale = 0
                self.effective_scale = 0
            if self.target is not None and not self.halted and scale > 0:
                candidate += np.clip(self.target - q, -0.7 * scale * 0.004, 0.7 * scale * 0.004)
                probe = mujoco.MjData(self.model)
                probe.qpos[:] = self.data.qpos
                probe.qpos[self.qids] = candidate
                mujoco.mj_forward(self.model, probe)
                problem = self._collision(probe)
                if problem:
                    self.target = None
                    self.reason = problem
                    candidate = q
                elif self._safe_scale(probe) <= 0:
                    candidate = q
                    self.reason = 'hand in path · paused'
            elif self.target is not None and scale <= 0:
                self.reason = 'hand in path · paused' if self.hands_list else 'motion paused'
            # Hold all arm joints kinematically while integrating free-body contacts.
            self.data.qpos[self.qids] = candidate
            self.data.qvel[self.dofs] = 0
            mujoco.mj_step(self.model, self.data)
            self.data.qpos[self.qids] = candidate
            self.data.qvel[self.dofs] = 0
            mujoco.mj_forward(self.model, self.data)
            if self.held:
                addr = self.model.joint(self.held+'_free').qposadr[0]
                self.data.qpos[addr:addr+3] = self.data.site_xpos[self.tcp]
                quat = np.empty(4)
                rotation = self.data.site_xmat[self.tcp].reshape(3, 3) @ self.held_rotation
                mujoco.mju_mat2Quat(quat, rotation.ravel())
                self.data.qpos[addr+3:addr+7] = quat
                dof = self.model.joint(self.held+'_free').dofadr[0]
                self.data.qvel[dof:dof+6] = 0
                mujoco.mj_forward(self.model, self.data)

            if not self.trace or self.data.time - self.trace[-1]['time_s'] >= .04 - 1e-9:
                self.trace.append({'time_s':float(self.data.time), 'joints':candidate.tolist(),
                                   'arm_position':list(self.position),
                                   'links':[self.data.xpos[b].tolist() for b in self.robot_bodies] + [list(self.position)],
                                   'objects':[vars(o) for o in self.objects()], 'hands':[vars(h) for h in self.hands()],
                                   'speed_scale':self.effective_scale, 'stopped':self.halted})
                if len(self.trace) > 15000:
                    self.trace = self.trace[-15000:]

    def replay(self):
        with self.lock:
            return {'version':1, 'mode':'simulation', 'seed':self.seed, 'frame':'marker centre; metres; z up',
                    'arm_base_offset':ARM_BASE_OFFSET, 'frames':list(self.trace), 'events':list(self.events)}

    def snapshot(self):
        """Existing WorldState scene schema for mission-control consumers."""
        self.world.update(objects=self.objects(), hands=self.hands(), arm_position=self.position)
        return self.world.snapshot()

    def randomise(self, seed=0):
        if self.command_lock.locked():
            raise ValueError('Randomise only while idle')
        with self.lock:
            rng = np.random.default_rng(seed)
            self.seed = seed
            for name, (_, position, _) in OBJECTS.items():
                addr = self.model.joint(name+'_free').qposadr[0]
                self.data.qpos[addr:addr+3] = np.array(position) + [rng.uniform(-.02,.02),rng.uniform(-.02,.02),0]
            mujoco.mj_forward(self.model,self.data)

    def _run(self):
        while not self.shutdown.is_set():
            start = time.monotonic()
            self.step()
            self.shutdown.wait(max(0, 0.004 - (time.monotonic() - start)))

    def solve_ik(self, target):
        if not valid_position(target) or not (-0.4 <= target[0] <= 0.5 and -0.4 <= target[1] <= 0.4 and 0.015 <= target[2] <= 0.5):
            raise ValueError('Target outside simulation workspace (metres, arm-base frame)')
        with self.lock:
            qpos = self.data.qpos.copy()
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = qpos
        jac = np.zeros((3, self.model.nv))
        # Position-only IK: five arm DOFs, gripper stays independent.
        best = None
        for seed in [qpos[self.qids[:5]], [0, -0.5, 1.0, 0.6, 0], [0.6, 0.3, -0.5, -0.8, 0], [-0.6, -0.8, 1.1, 0.6, 0]]:
            probe.qpos[self.qids[:5]] = seed
            for _ in range(250):
                mujoco.mj_forward(self.model, probe)
                error = np.array(target) - probe.site_xpos[self.tcp]
                norm = np.linalg.norm(error)
                if norm < 0.002:
                    if self._collision(probe) is None:
                        return probe.qpos[self.qids].copy()
                    break
                mujoco.mj_jacSite(self.model, probe, jac, None, self.tcp)
                j = jac[:, self.dofs[:5]]
                delta = j.T @ np.linalg.solve(j @ j.T + np.eye(3)*0.0004, error)
                probe.qpos[self.qids[:5]] = np.clip(probe.qpos[self.qids[:5]] + np.clip(delta, -0.15, 0.15), self.limits[:5, 0], self.limits[:5, 1])
                if best is None or norm < best:
                    best = norm
        raise ValueError('No collision-free IK solution within joint limits')

    def _move_joints(self, values, scale=1.0, timeout=20):
        if 'command_refused' in self.faults:
            self.reason = 'injected command refusal'
            return False
        if not math.isfinite(scale) or not 0 < scale <= 1:
            return False
        values = np.asarray(values, dtype=float)
        if values.shape != (6,) or not np.all(np.isfinite(values)) or np.any(values < self.limits[:, 0]) or np.any(values > self.limits[:, 1]):
            raise ValueError('Joint targets must be six finite radians within URDF limits')
        if not self.command_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                if self.halted or self.safety.stopped:
                    return False
                self._command_scale = scale
                self.target = values.copy()
                self.reason = 'moving'
            clock = time.monotonic if self.realtime else lambda: float(self.data.time)
            deadline = clock() + timeout
            while clock() < deadline and not self.shutdown.is_set():
                with self.lock:
                    if self.halted or self.safety.stopped or self.target is None:
                        return False
                    if np.max(np.abs(self.data.qpos[self.qids] - values)) < 0.003:
                        self.target = None
                        self.reason = 'ready'
                        return True
                    # Per-command cap is separate from external watchdog speed scale.
                    self._command_scale = scale
                if not self.realtime:
                    self.step()
                else:
                    self.shutdown.wait(0.01)
            with self.lock:
                self.target = None
                self.reason = 'command timed out'
            return False
        finally:
            self._command_scale = 1.0
            self.command_lock.release()

    def move_to(self, target, speed_scale):
        try:
            joints = self.solve_ik(target)
        except ValueError as exc:
            with self.lock:
                self.reason = str(exc)
            return False
        return self._move_joints(joints, speed_scale)

    def move_joints(self, values):
        return self._move_joints(values)

    def grip(self, closed):
        if not isinstance(closed, bool):
            raise ValueError('closed must be a boolean')
        with self.lock:
            if self.halted or self.safety.stopped or self._safe_scale(self.data) <= 0 or self.speed_scale <= 0:
                return False
            joints = self.data.qpos[self.qids].copy()
            joints[5] = 0.0 if closed else 0.8
        if not self._move_joints(joints):
            return False
        with self.lock:
            if self.halted or self.safety.stopped:
                return False
            self.gripping = closed
            if closed:
                near = sorted((math.dist(o.position, self.position), o.id) for o in self.objects())
                if near and near[0][0] < 0.035:
                    self.held = near[0][1]
                    self.held_rotation = self.data.site_xmat[self.tcp].reshape(3, 3).T @ self.data.body(self.held).xmat.reshape(3, 3)
                    self.event('Ideal grasp attached '+self.held)
                else:
                    self.reason = 'gripper closed; no object in grasp range'
                    return False
            else:
                self.held = None
                self.event('Gripper released')
            return True

    def state(self):
        with self.lock:
            return {'mode': 'simulation', 'robot': 'SO-101', 'frame':'marker centre; metres; z up', 'arm_base_offset':ARM_BASE_OFFSET, 'time_s': round(self.data.time, 3),
                    'joints': {n: {'position': float(q), 'lower': float(l[0]), 'upper': float(l[1])}
                               for n, q, l in zip(JOINTS, self.data.qpos[self.qids], self.limits)},
                    'scene': {'arm_position': self.position, 'objects': [vars(o) for o in self.objects()],
                              'hands': [vars(h) for h in self.hands()]},
                    'stopped': self.halted or self.safety.stopped, 'busy': self.command_lock.locked(),
                    'gripping': self.gripping, 'held': self.held, 'speed_scale': self.effective_scale,
                    'reason': self.reason, 'events': list(self.events)}

    def close(self):
        self.halt()
        self.shutdown.set()
        if self.thread:
            self.thread.join(timeout=2)
