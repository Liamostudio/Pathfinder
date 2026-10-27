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
    def __init__(self, start: Vec3 = (0.0, 0.0, 0.2)) -> None:
        self.position = start
        self._lock = threading.RLock()
        self.gripping = False
        self.halted = False
        self.speed_scale = 1.0
        self.log: list[tuple[str, object]] = []

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
