"""The orchestrator's constrained tool surface: get_scene, pick, place, stop, say.

A model (Muse Spark, Glimmer) only ever calls these. Every motion goes through the safety layer.
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Callable

from .adapters.base import Arm, Tracker
from .safety import Safety, valid_position
from .world import Hand, TrackedObject, Vec3, WorldState

HOVER_M = 0.10
POLL_S = 0.05            # how often a paused move re-checks the hands
REMIND_S = 20.0          # "still waiting" reminder interval


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
    poll_s, remind_s = POLL_S, REMIND_S

    def __init__(self, world: WorldState, tracker: Tracker, arm: Arm, safety: Safety,
                 say: Callable[[str], None] = print, zones=None, announce: Callable[[str], None] | None = None) -> None:
        self.world, self.tracker, self.arm, self.safety, self._say = world, tracker, arm, safety, say
        self.zones = zones                      # an optional ManualZoneGuard: hard no-go cubes drawn by a person
        self._announce_to = announce            # short local status lines ("Pausing: a hand is in the way"); no model is involved
        self._block_reason: str | None = None
        self._action_lock = threading.Lock()
        self._cancelled = threading.Event()     # a person's "cancel": ends the current instruction, latches nothing
        self.waits_for_hands = True             # False only for the scripted fake Deck backend, which has no live watchdog to wait for

    def announce(self, text: str) -> None:
        """A fixed, local phrase for the user. Goes to `announce` if given (Deck logs it), else to `say`."""
        (self._announce_to or self._say)(text)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def begin_instruction(self) -> None:
        """A new instruction from a person clears an earlier cancel."""
        self._cancelled.clear()

    def cancel(self) -> str:
        """End the current instruction without latching anything: a move in flight is dropped (the arm holds where it is)
        and a wait for a hand to move away ends. Does not clear a stop."""
        self._cancelled.set()
        cancel = getattr(self.arm, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:                    # the arm may be stopped or busy; the flag alone already ends the wait
                pass
        return "cancelled"

    def _refresh(self) -> None:
        self.world.update(objects=self.tracker.objects(), hands=self.tracker.hands(),
                          arm_position=self.arm.position)

    def _hands_now(self) -> list[Hand]:
        hands = list(self.tracker.hands())
        self.world.update(hands=hands)
        return hands

    def _wait_for_clear(self, path: tuple[Vec3, Vec3]) -> float:
        """The speed scale for `path` once no hand is in the way, or 0.0 to give up. A hand in the way is waited out, with no time limit:
        it ends when the hand leaves, a person says stop or cancel, or the safety layer latches (a fault). Every other zero speed
        (a latch, bad geometry) is a refusal. Says so once on pausing, now and then while waiting, and once on resuming."""
        waiting = False
        reminded = 0.0
        hands = [Hand(**h) for h in self.world.snapshot()['hands']]
        while True:
            if self.safety.stopped:
                return 0.0
            if self._cancelled.is_set():
                self._block_reason = "cancelled"
                return 0.0
            scale = self.safety.allowed_speed(hands, path)
            if scale > 0.0:
                if waiting:
                    self.announce("Clear, carrying on")
                return scale
            if not self.waits_for_hands or not self.safety.hand_in_the_way(hands, path):
                return 0.0
            now = time.monotonic()
            if not waiting:
                waiting, reminded = True, now
                self.announce("Pausing: a hand is in the way")
            elif now - reminded >= self.remind_s:
                reminded = now
                self.announce("Still waiting for the way to clear")
            self._cancelled.wait(self.poll_s)
            hands = self._hands_now()

    def _move(self, target: Vec3) -> bool:
        """Check safety before commanding; return True only after acknowledged completion. A hand in the way is waited out."""
        self._refresh()
        path = (self.arm.position, target)
        if self.safety.stopped:
            return False
        if self.zones is not None:
            reason = self.zones.violation(*path)
            if reason:
                self._block_reason = reason
                return False
        scale = self._wait_for_clear(path)
        if scale <= 0.0 or self.safety.stopped:
            return False
        completed = self.arm.move_to(target, scale) is True
        self.world.update(arm_position=self.arm.position)
        if not completed and self._cancelled.is_set():
            self._block_reason = "cancelled"
        return completed

    def _grip(self, closed: bool) -> bool:
        self._refresh()
        if self.safety.stopped:
            return False
        if self._wait_for_clear((self.arm.position, self.arm.position)) <= 0:
            return False
        if self.safety.stopped:
            return False
        return self.arm.grip(closed) is True

    def _aim_gripper(self, yaw_deg: float | None) -> None:
        """Hand a measured grasp yaw to an arm that can take one; always sets it so a stale yaw never carries to the next pick.
        Arms without `set_grasp_yaw` pick as before. This is not a safety input: every move still goes through `_move`."""
        setter = getattr(self.arm, 'set_grasp_yaw', None)
        if callable(setter):
            setter(yaw_deg)

    def get_scene(self) -> dict:
        self._refresh()
        return self.world.snapshot()

    @_one_action
    def pick(self, id: str) -> str:
        self._block_reason = None
        if self.safety.stopped:
            return "blocked: stopped"
        if self._cancelled.is_set():
            return "blocked: cancelled"
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
        self._aim_gripper(obj.get('grasp_yaw_deg'))
        if not (self._move(above) and self._move(target) and self._grip(True)):
            return f"blocked: {self._block_reason or 'safety gate or arm refused the action'}"
        self.world.set_held(TrackedObject(**obj), True, self.arm.position)
        if not self._move(above):
            return f"blocked: holding {obj['label']}; retreat incomplete"
        return f"picked {obj['label']}"

    @_one_action
    def place(self, id: str, loc: Vec3) -> str:
        self._block_reason = None
        if self.safety.stopped:
            return "blocked: stopped"
        if self._cancelled.is_set():
            return "blocked: cancelled"
        self._refresh()
        obj = next((o for o in self.world.snapshot()['objects'] if o['id'] == id), None)
        if obj is None or not obj['held']:
            return f"error: {id} is not held"
        if not valid_position(loc):
            return "blocked: invalid target coordinates"
        above = (loc[0], loc[1], loc[2] + HOVER_M)
        if not (self._move(above) and self._move(loc) and self._grip(False)):
            return f"blocked: {self._block_reason or 'safety gate or arm refused the action'}"
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
