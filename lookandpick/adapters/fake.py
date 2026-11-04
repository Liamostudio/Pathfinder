"""Fake adapters for development against scripted data."""
from __future__ import annotations

import math
import threading

from ..world import Hand, TrackedObject, Vec3


class FakeTracker:
    def __init__(self, objects: list[TrackedObject] | None = None, hands: list[Hand] | None = None) -> None:
        self._objects = objects if objects is not None else [
            TrackedObject("obj1", "red cube", (0.25, 0.10, 0.0)),
            TrackedObject("obj2", "blue mug", (0.20, -0.15, 0.0)),
        ]
        self._hands = hands or []

    def objects(self) -> list[TrackedObject]:
        return self._objects

    def hands(self) -> list[Hand]:
        return self._hands

    def set_hands(self, hands: list[Hand]) -> None:
        self._hands = hands


class SimArm:
    simulated = True

    def __init__(self, start: Vec3 = (0.0, 0.0, 0.2)) -> None:
        self.position = start
        self._lock = threading.RLock()
        self.gripping = False
        self.halted = False
        self.speed_scale = 1.0
        self.grasp_yaw_deg: float | None = None
        self.log: list[tuple[str, object]] = []

    def set_grasp_yaw(self, yaw_deg: float | None) -> None:
        with self._lock:
            self.grasp_yaw_deg = yaw_deg
            self.log.append(("grasp_yaw", yaw_deg))

    def move_to(self, target: Vec3, speed_scale: float) -> bool:
        with self._lock:
            speed_scale = min(speed_scale, self.speed_scale)
            if self.halted or speed_scale <= 0.0:
                self.log.append(("blocked", target))
                return False
            self.position = target
            self.log.append(("move", (target, speed_scale)))
            return True

    def grip(self, closed: bool) -> bool:
        with self._lock:
            if self.halted or self.speed_scale <= 0:
                self.log.append(("blocked_grip", closed))
                return False
            self.gripping = closed
            self.log.append(("grip", closed))
            return True

    def reachable(self, point) -> bool:
        x, y, z = point
        return -0.4 <= x <= 0.5 and -0.4 <= y <= 0.4 and 0.0 <= z <= 0.5

    def resume(self) -> None:
        with self._lock:
            self.halted = False
            self.speed_scale = 1.0
            self.log.append(("resume", None))

    # Rest: a fixed fold-back position for the fake arm (the simulator has its own joint-space Rest).
    rest_position: Vec3 = (0.0, -0.12, 0.2)

    def rest(self) -> bool:
        with self._lock:
            if self.halted or self.speed_scale <= 0.0:
                self.log.append(("blocked_rest", None))
                return False
            self.position = self.rest_position
            self.log.append(("rest", None))
            return True

    def cancel(self) -> None:
        with self._lock:
            self.log.append(("cancel", None))

    def halt(self) -> None:
        with self._lock:
            self.halted = True
            self.speed_scale = 0.0
            self.log.append(("halt", None))

    def set_speed_scale(self, scale: float) -> None:
        if not math.isfinite(scale) or not 0 <= scale <= 1:
            raise ValueError('Invalid speed scale')
        with self._lock:
            self.speed_scale = 0.0 if self.halted else scale
