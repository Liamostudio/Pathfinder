"""The orchestrator's constrained tool surface: get_scene, pick, place, stop, say.

A model (Muse Spark, Glimmer) only ever calls these. Every motion goes through the safety layer.
"""
from __future__ import annotations

import math
import threading
import time
from functools import wraps
from typing import Callable

from .adapters.base import Arm, Tracker
from .safety import Safety, valid_position
from .world import Hand, TrackedObject, Vec3, WorldState

HOVER_M = 0.10
LOOK_S = 10.0            # how long to look again for an object that left the view
MAX_GRIPS = 3            # grip attempts before giving up
MOVED_M = 0.01           # a target that moved more than this is re-aimed at
SPOT_CLEARANCE_M = 0.04  # a drop spot closer than this to another object is "taken" (cubes are 3.6 cm)
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
        self.relook = None                      # a callable that asks the scene to look at the table again (SAM); set by the Runtime
        self.look_s, self.max_grips, self.moved_m, self.spot_clearance_m = LOOK_S, MAX_GRIPS, MOVED_M, SPOT_CLEARANCE_M
        self.verify_lift = False                # True with a camera tracker: after closing, check the object rose with the gripper
        self.gave_up = False                    # the instruction ended for a reason that waiting will not change; the runtime folds back to Rest
        self.holds_through_stops = False        # True under the Runtime: a stop mid-action HOLDS it (a person says 'carry on' or 'cancel'; a fault recovers)
        self._held_origin: dict[str, Vec3] = {}  # where each held object was picked up, so Rest can put it back
        self.waits_for_hands = True             # False only for the scripted fake Deck backend, which has no live watchdog to wait for

    def announce(self, text: str) -> None:
        """A fixed, local phrase for the user. Goes to `announce` if given (Deck logs it), else to `say`."""
        (self._announce_to or self._say)(text)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def begin_instruction(self) -> None:
        """A new instruction from a person clears an earlier cancel (and an earlier give-up)."""
        self._cancelled.clear()
        self.gave_up = False

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
        stop_noted = False
        reminded = 0.0
        hands = [Hand(**h) for h in self.world.snapshot()['hands']]
        while True:
            if self._cancelled.is_set():
                self._block_reason = "cancelled"
                return 0.0
            if self.safety.stopped or self.safety.holding:
                if not self.holds_through_stops:
                    return 0.0
                if not stop_noted:
                    stop_noted = True
                    if self.safety.stopped and self.safety.stop_kind == "person":
                        self.announce("Stopped. Say carry on to continue, or cancel")
                    else:
                        self.announce("Paused: recovering from a fault")
                self._cancelled.wait(self.poll_s)
                continue
            if stop_noted:
                stop_noted = False
                hands = self._hands_now()
                self.announce("Carrying on")
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

    def _arm_timed_out(self) -> bool:
        """The arm says its last move made no progress (a real fault, unlike waiting for a hand)."""
        state = getattr(self.arm, "state", None)
        try:
            return callable(state) and state().get("reason") == "command timed out"
        except Exception:
            return False

    def _move(self, target: Vec3) -> bool:
        return self._segment(target, lambda scale: self.arm.move_to(target, scale))

    def _segment(self, target: Vec3, command: Callable[[float], object]) -> bool:
        """Check safety before commanding; return True only after acknowledged completion. A hand in the way is waited out; under the Runtime
        a stop mid-move is waited out too (a person's 'carry on', or an automatic recovery from a fault), then the move is issued again from where
        the arm stopped. A move that makes no progress is a fault stop."""
        self._refresh()
        if self.safety.stopped and not self.holds_through_stops:
            return False
        while True:
            path = (self.arm.position, target)
            if self.zones is not None:
                reason = self.zones.violation(*path)
                if reason:
                    self._block_reason = reason
                    return False
            scale = self._wait_for_clear(path)
            if scale <= 0.0 or (self.safety.stopped and not self.holds_through_stops):
                return False
            epoch = self.safety.stop_count
            try:
                completed = command(scale) is True
            except TimeoutError:
                completed = False
                self.safety.stop("fault", "a move made no progress")
                self.arm.halt()
            self.world.update(arm_position=self.arm.position)
            if completed:
                return True
            if self._cancelled.is_set():
                self._block_reason = "cancelled"
                return False
            if not self.safety.stopped and not self.safety.holding and self._arm_timed_out():
                self.safety.stop("fault", "a move made no progress")
                self.arm.halt()
            if self.holds_through_stops and (self.safety.stop_count != epoch or self.safety.stopped or self.safety.holding):
                continue                                  # interrupted by a stop: wait it out, then carry on from here
            return False

    def _grip(self, closed: bool) -> bool:
        self._refresh()
        if self.safety.stopped and not self.holds_through_stops:
            return False
        while True:
            if self._wait_for_clear((self.arm.position, self.arm.position)) <= 0:
                return False
            epoch = self.safety.stop_count
            if self.safety.stopped and not self.holds_through_stops:
                return False
            if self.arm.grip(closed) is True:
                return True
            if self.holds_through_stops and not self._cancelled.is_set() and (
                    self.safety.stop_count != epoch or self.safety.stopped or self.safety.holding):
                continue
            return False

    def _aim_gripper(self, yaw_deg: float | None) -> None:
        """Hand a measured grasp yaw to an arm that can take one; always sets it so a stale yaw never carries to the next pick.
        Arms without `set_grasp_yaw` pick as before. This is not a safety input: every move still goes through `_move`."""
        setter = getattr(self.arm, 'set_grasp_yaw', None)
        if callable(setter):
            setter(yaw_deg)

    def get_scene(self) -> dict:
        self._refresh()
        return self.world.snapshot()

    # -- finding the target again, and giving up only for reasons that waiting or trying again will not fix --------------------------------
    def _unreachable(self) -> bool:
        """The arm says the last target is out of reach (outside its workspace or no collision-free solution)."""
        state = getattr(self.arm, "state", None)
        try:
            reason = str(state().get("reason", "")) if callable(state) else ""
        except Exception:
            return False
        return reason.startswith(("Target outside", "No collision-free"))

    def _give_up(self, result: str, sentence: str) -> str:
        """End the instruction for a reason that waiting will not change: one sentence for the user (why, and what would help), and the runtime then
        folds the arm back to Rest. The tool result carries the reason back to the model."""
        self.gave_up = True
        self.announce(sentence)
        return f"blocked: {result}"

    def _look_for(self, id: str, label: str) -> dict | None:
        """The object's fresh record, or None if it cannot be found within `look_s`. Says so, and asks the scene to look again (SAM) while it waits."""
        self._refresh()
        found = next((o for o in self.world.snapshot()['objects'] if o['id'] == id), None)
        if found is not None:
            return found
        self.announce(f"Lost sight of the {label}, looking again")
        deadline = time.monotonic() + self.look_s
        while time.monotonic() < deadline:
            if self.safety.stopped or self._cancelled.is_set():
                return None
            if self.relook is not None:
                try:
                    self.relook()
                except Exception:                  # the scene may be unavailable for a moment; keep looking until the time is up
                    pass
            self._refresh()
            found = next((o for o in self.world.snapshot()['objects'] if o['id'] == id), None)
            if found is not None:
                return found
            self._cancelled.wait(0.5)
        return None

    def _held_by_vision(self, id: str) -> bool:
        """Did the object rise with the gripper? Only meaningful with a camera tracker (`verify_lift`); a simulator reports its own grip."""
        self._refresh()
        obj = next((o for o in self.world.snapshot()['objects'] if o['id'] == id), None)
        if obj is None:
            return True                            # hidden by the gripper: treat as held
        return obj['held'] or math.dist(obj['position'], self.arm.position) < 0.04          # it came up with the gripper

    def _cant_find(self, label: str) -> str:
        return self._give_up(f"cannot find the {label} after looking for {self.look_s:.0f} s",
                             f"I can't find the {label}; put it back in view and I'll try again.")

    def _pick_failed(self, label: str) -> str:
        if self._unreachable():
            return self._give_up(f"the {label} is out of reach", f"I can't reach the {label} from here; move it closer and I'll try again.")
        return f"blocked: {self._block_reason or 'safety gate or arm refused the action'}"

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
        label = obj['label']
        if not valid_position(obj['position']):
            return "blocked: invalid target coordinates"
        for attempt in range(1, self.max_grips + 1):
            obj = self._look_for(id, label)                       # fresh position before the approach
            if obj is None:
                return "blocked: cancelled" if self._cancelled.is_set() else "blocked: stopped" if self.safety.stopped else self._cant_find(label)
            target = obj['position']
            if not valid_position(target):
                return "blocked: invalid target coordinates"
            above = (target[0], target[1], target[2] + HOVER_M)
            self._aim_gripper(obj.get('grasp_yaw_deg'))
            if not self._move(above):
                return self._pick_failed(label)
            fresh = self._look_for(id, label)                     # and again before the final descent: it may have been moved
            if fresh is None:
                return "blocked: cancelled" if self._cancelled.is_set() else "blocked: stopped" if self.safety.stopped else self._cant_find(label)
            if math.dist(fresh['position'], target) > self.moved_m:
                obj, target = fresh, fresh['position']
                above = (target[0], target[1], target[2] + HOVER_M)
                self._aim_gripper(obj.get('grasp_yaw_deg'))
                if not self._move(above):
                    return self._pick_failed(label)
            if not self._move(target):
                return self._pick_failed(label)
            if self._grip(True) and (not self.verify_lift or self._lifted(id, above)):
                break
            if self.safety.stopped or self._cancelled.is_set():
                return "blocked: cancelled" if self._cancelled.is_set() else f"blocked: {self._block_reason or 'safety gate or arm refused the action'}"
            if attempt < self.max_grips:                          # a missed grip: open, back off, look again, try again
                self.announce("Missed it, trying again")
                self._grip(False)
                if not self._move(above):
                    return self._pick_failed(label)
        else:
            return self._give_up(f"missed the {label} {self.max_grips} times", f"I keep missing the {label}; check it is within reach and I'll try again.")
        self.world.set_held(TrackedObject(**obj), True, self.arm.position)
        self._held_origin[obj['id']] = (float(target[0]), float(target[1]), float(target[2]))
        if not self._move(above):
            return f"blocked: holding {obj['label']}; retreat incomplete"
        self.gave_up = False
        return f"picked {obj['label']}"

    def _lifted(self, id: str, above: Vec3) -> bool:
        """After closing on it with a camera tracker: lift a little and see whether the object came too (it did not if it is still on the table)."""
        if not self._move((above[0], above[1], above[2] - HOVER_M / 2)):
            return False
        return self._held_by_vision(id)

    @_one_action
    def place(self, id: str, loc: Vec3) -> str:
        self._block_reason = None
        if self.safety.stopped:
            return "blocked: stopped"
        if self._cancelled.is_set():
            return "blocked: cancelled"
        self._refresh()
        snapshot = self.world.snapshot()['objects']
        obj = next((o for o in snapshot if o['id'] == id), None)
        if obj is None or not obj['held']:
            return f"error: {id} is not held"
        if not valid_position(loc):
            return "blocked: invalid target coordinates"
        taken = next((o for o in snapshot if o['id'] != id and math.dist(o['position'][:2], loc[:2]) < self.spot_clearance_m), None)
        if taken is not None:
            return f"blocked: that spot is taken by the {taken['label']}; choose a free spot beside it"
        above = (loc[0], loc[1], loc[2] + HOVER_M)
        if not (self._move(above) and self._move(loc) and self._grip(False)):
            if self._unreachable():
                return self._give_up(f"the drop spot is out of reach", f"I can't reach that spot from here; choose a spot nearer the arm and I'll try again.")
            return f"blocked: {self._block_reason or 'safety gate or arm refused the action'}"
        self.world.set_held(TrackedObject(**obj), False, self.arm.position)
        self._held_origin.pop(obj['id'], None)
        if not self._move(above):
            return f"blocked: released {obj['label']}; retreat incomplete"
        self.gave_up = False
        return f"placed {obj['label']}"

    def stop(self) -> str:
        self.safety.stop("person", "STOP requested")
        self.arm.halt()
        return "stopped"

    def go_rest(self) -> str:
        """Fold the arm back to its Rest pose. First puts any held object down where it was picked up, if that spot is clear; otherwise goes to
        Rest holding it and says so. Checked like any other move (hands, no-go cubes), so it can pause and carry on. Never runs while a person's
        STOP is latched. Not a model tool: the runtime calls it after cancel, a reset, or an instruction it could not finish."""
        if self.safety.stopped:
            return "blocked: stopped"
        rest = getattr(self.arm, "rest", None)
        if not callable(rest):
            return "blocked: this arm has no rest pose"
        self._block_reason = None
        self._refresh()
        held = next((o for o in self.world.snapshot()['objects'] if o['held']), None)
        if held is not None:
            origin = self._held_origin.get(held['id'])
            if origin is not None and self._spot_is_clear(held['id'], origin):
                above = (origin[0], origin[1], origin[2] + HOVER_M)
                if not (self._move(above) and self._move(origin) and self._grip(False)):
                    return f"blocked: {self._block_reason or 'could not put the ' + held['label'] + ' down'}"
                self.world.set_held(TrackedObject(**held), False, self.arm.position)
                self._held_origin.pop(held['id'], None)
                if not self._move(above):
                    return f"blocked: {self._block_reason or 'retreat incomplete'}"
            else:
                self.announce(f"Going to rest holding the {held['label']}")
        self.announce("Heading back to rest")
        target = tuple(getattr(self.arm, "rest_position", None) or self.arm.position)
        if not self._segment(target, lambda scale: rest()):
            return f"blocked: {self._block_reason or 'could not reach rest'}"
        return "at rest"

    def _spot_is_clear(self, held_id: str, spot: Vec3, clearance_m: float = 0.05) -> bool:
        """No other object within `clearance_m` (across the table) of where the held one was picked up, and no no-go cube in the way."""
        for o in self.world.snapshot()['objects']:
            if o['id'] != held_id and math.dist(o['position'][:2], spot[:2]) < clearance_m:
                return False
        return self.zones is None or self.zones.violation(self.arm.position, spot) is None

    def say(self, text: str) -> None:
        self._say(text)
