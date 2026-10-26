"""The orchestrator's constrained tool surface: get_scene, pick, place, stop, say.

A model (Muse Spark, Glimmer) only ever calls these. Every motion goes through the safety layer.
"""
from __future__ import annotations

from typing import Callable

from .adapters.base import Arm, Tracker
from .safety import Safety
from .world import Vec3, WorldState

HOVER_M = 0.10


class Tools:
    def __init__(self, world: WorldState, tracker: Tracker, arm: Arm, safety: Safety,
                 say: Callable[[str], None] = print) -> None:
        self.world, self.tracker, self.arm, self.safety, self._say = world, tracker, arm, safety, say

    def _refresh(self) -> None:
        self.world.update(objects=self.tracker.objects(), hands=self.tracker.hands())
        self.world.arm_position = self.arm.position

    def _move(self, target: Vec3) -> bool:
        """Move via the safety layer. Returns False if blocked, in which case the arm is not commanded."""
        self._refresh()
        scale = self.safety.allowed_speed(self.world.hands, (self.arm.position, target))
        if scale <= 0.0:
            return False
        self.arm.move_to(target, scale)
        return True

    def get_scene(self) -> dict:
        self._refresh()
        return self.world.snapshot()

    def pick(self, id: str) -> str:
        self._refresh()
        obj = self.world.objects.get(id)
        if obj is None:
            return f"error: no object with id {id}"
        above = (obj.position[0], obj.position[1], obj.position[2] + HOVER_M)
        if not (self._move(above) and self._move(obj.position)):
            return "blocked: stopped or a hand is too close"
        self.arm.grip(True)
        obj.held = True
        self._move(above)
        return f"picked {obj.label}"

    def place(self, id: str, loc: Vec3) -> str:
        obj = self.world.objects.get(id)
        if obj is None or not obj.held:
            return f"error: {id} is not held"
        above = (loc[0], loc[1], loc[2] + HOVER_M)
        if not (self._move(above) and self._move(loc)):
            return "blocked: stopped or a hand is too close"
        self.arm.grip(False)
        obj.held = False
        obj.position = loc
        self._move(above)
        return f"placed {obj.label}"

    def stop(self) -> str:
        self.safety.stop()
        self.arm.halt()
        return "stopped"

    def say(self, text: str) -> None:
        self._say(text)
