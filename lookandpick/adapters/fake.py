"""Fake adapters for development against scripted data."""
from __future__ import annotations

from ..world import Hand, TrackedObject, Vec3


class FakeTracker:
    def __init__(self, objects: list[TrackedObject] | None = None, hands: list[Hand] | None = None) -> None:
        self._objects = objects or [
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
        self.gripping = False
        self.halted = False
        self.log: list[tuple[str, object]] = []

    def move_to(self, target: Vec3, speed_scale: float) -> None:
        if self.halted or speed_scale <= 0.0:
            self.log.append(("blocked", target))
            return
        self.position = target
        self.log.append(("move", (target, speed_scale)))

    def grip(self, closed: bool) -> None:
        self.gripping = closed
        self.log.append(("grip", closed))

    def halt(self) -> None:
        self.halted = True
        self.log.append(("halt", None))
