"""Shared world state. Loops write to it; nothing calls another model directly."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]  # metres, arm-base frame (set by the ArUco calibration)


@dataclass
class TrackedObject:
    id: str
    label: str
    position: Vec3
    held: bool = False


@dataclass
class Hand:
    id: str
    position: Vec3


@dataclass
class WorldState:
    objects: dict[str, TrackedObject] = field(default_factory=dict)
    hands: list[Hand] = field(default_factory=list)
    arm_position: Vec3 = (0.0, 0.0, 0.2)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, objects: list[TrackedObject] | None = None, hands: list[Hand] | None = None) -> None:
        with self._lock:
            if objects is not None:
                self.objects = {o.id: o for o in objects}
            if hands is not None:
                self.hands = list(hands)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "objects": [vars(o).copy() for o in self.objects.values()],
                "hands": [vars(h).copy() for h in self.hands],
                "arm_position": self.arm_position,
            }
