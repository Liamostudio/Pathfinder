"""Shared world state. Loops write to it; nothing calls another model directly."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

Vec3 = tuple[float, float, float]  # metres, arm-base frame (set by the ArUco calibration)
OPTIONAL_OBJECT_FIELDS = ("grasp_yaw_deg", "size_m")  # left out of the scene dict until measured, so unmeasured objects look as before


@dataclass
class TrackedObject:
    id: str
    label: str
    position: Vec3
    held: bool = False
    grasp_yaw_deg: float | None = None                     # gripper yaw in the marker frame; None = any yaw will do (a cube)
    size_m: tuple[float, float, float] | None = None       # measured length, width, height (upper bounds); None = not measured


@dataclass
class Hand:
    id: str
    position: Vec3
    radius_m: float = 0.0  # conservative envelope around the projected landmarks


@dataclass
class WorldState:
    objects: dict[str, TrackedObject] = field(default_factory=dict)
    hands: list[Hand] = field(default_factory=list)
    arm_position: Vec3 = (0.0, 0.0, 0.2)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, objects: list[TrackedObject] | None = None, hands: list[Hand] | None = None,
               arm_position: Vec3 | None = None) -> None:
        with self._lock:
            if arm_position is not None:
                self.arm_position = arm_position
                self.objects = {key: replace(obj, position=arm_position) if obj.held else obj
                                for key, obj in self.objects.items()}
            if objects is not None:
                # Grasp state belongs to the controller, not camera detections.
                # A held object can disappear behind the gripper or leave the frame.
                held = {key: replace(obj, position=self.arm_position)
                        for key, obj in self.objects.items() if obj.held}
                self.objects = {o.id: replace(o, held=False) for o in objects}
                self.objects.update(held)
            if hands is not None:
                self.hands = [replace(h) for h in hands]

    def set_held(self, obj: TrackedObject, held: bool, position: Vec3) -> None:
        """Record an acknowledged gripper action without mutating tracker data."""
        with self._lock:
            self.objects[obj.id] = replace(obj, held=held, position=position)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "objects": [{k: v for k, v in vars(o).items() if v is not None or k not in OPTIONAL_OBJECT_FIELDS}
                            for o in self.objects.values()],
                "hands": [vars(h).copy() for h in self.hands],
                "arm_position": self.arm_position,
            }
