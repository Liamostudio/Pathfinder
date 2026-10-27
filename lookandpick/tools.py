"""The orchestrator's constrained tool surface: get_scene, pick, place, stop, say.

A model (Muse Spark, Glimmer) only ever calls these. Every motion goes through the safety layer.
"""
from __future__ import annotations

import threading
from functools import wraps
from typing import Callable

from .adapters.base import Arm, Tracker
from .safety import Safety, valid_position
from .world import Hand, TrackedObject, Vec3, WorldState

HOVER_M = 0.10


def _one_action(method):
    """Serialise pick/place without ever making stop wait on a motion lock."""
    @wraps(method)
    def run(self, *args, **kwargs):
        if not self._action_lock.acquire(blocking=False):
            return "blocked: another action is in progress"
        try:
            return method(self, *args, **kwargs)
        finally:
            self._action_lock.release()
    return run


class Tools:
    def __init__(self, world: WorldState, tracker: Tracker, arm: Arm, safety: Safety,
                 say: Callable[[str], None] = print) -> None:
        self.world, self.tracker, self.arm, self.safety, self._say = world, tracker, arm, safety, say
        self._action_lock = threading.Lock()

    def _refresh(self) -> None:
        self.world.update(objects=self.tracker.objects(), hands=self.tracker.hands(),
                          arm_position=self.arm.position)

    def _move(self, target: Vec3) -> bool:
        """Check safety before commanding; return True only after acknowledged completion."""
        self._refresh()
        scene = self.world.snapshot()
        scale = self.safety.allowed_speed([Hand(**h) for h in scene['hands']], (self.arm.position, target))
        if scale <= 0.0:
            return False
        if self.safety.stopped:
            return False
        completed = self.arm.move_to(target, scale) is True
        self.world.update(arm_position=self.arm.position)
        return completed

    def _grip(self, closed: bool) -> bool:
        self._refresh()
        hands = [Hand(**h) for h in self.world.snapshot()['hands']]
        if self.safety.allowed_speed(hands, (self.arm.position, self.arm.position)) <= 0:
            return False
        if self.safety.stopped:
            return False
        return self.arm.grip(closed) is True

    def get_scene(self) -> dict:
        self._refresh()
        return self.world.snapshot()

    @_one_action
    def pick(self, id: str) -> str:
        if self.safety.stopped:
            return "blocked: stopped"
        self._refresh()
        objects = self.world.snapshot()['objects']
        if any(o['held'] for o in objects):
            return "error: already holding an object"
        obj = next((o for o in objects if o['id'] == id), None)
        if obj is None:
            return f"error: no object with id {id}"
        target = obj['position']
        if not valid_position(target):
            return "blocked: invalid target coordinates"
        above = (target[0], target[1], target[2] + HOVER_M)
        if not (self._move(above) and self._move(target) and self._grip(True)):
            return "blocked: safety gate or arm refused the action"
        self.world.set_held(TrackedObject(**obj), True, self.arm.position)
        if not self._move(above):
            return f"blocked: holding {obj['label']}; retreat incomplete"
        return f"picked {obj['label']}"

    @_one_action
    def place(self, id: str, loc: Vec3) -> str:
        if self.safety.stopped:
            return "blocked: stopped"
        self._refresh()
        obj = next((o for o in self.world.snapshot()['objects'] if o['id'] == id), None)
        if obj is None or not obj['held']:
            return f"error: {id} is not held"
        if not valid_position(loc):
            return "blocked: invalid target coordinates"
        above = (loc[0], loc[1], loc[2] + HOVER_M)
        if not (self._move(above) and self._move(loc) and self._grip(False)):
            return "blocked: safety gate or arm refused the action"
        self.world.set_held(TrackedObject(**obj), False, self.arm.position)
        if not self._move(above):
            return f"blocked: released {obj['label']}; retreat incomplete"
        return f"placed {obj['label']}"

    def stop(self) -> str:
        self.safety.stop()
        self.arm.halt()
        return "stopped"

    def say(self, text: str) -> None:
        self._say(text)
