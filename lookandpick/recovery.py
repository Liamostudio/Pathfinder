"""Automatic recovery from FAULT stops (a camera stall, detector loss, a move that made no progress), for the simulated arm.

A person's stop (the STOP button, space bar, spoken stop) is never touched here: only "carry on" or the reset button clears it.
For a fault stop the supervisor rebuilds the hand gate (which needs every camera to deliver fresh frames and the detector to run), holds the
arm at zero speed until the way has been clear for `clear_s` seconds, then lets it go, and the interrupted pick or place carries on from
where it stopped (the waiting move in `Tools` picks it up). It will do this at most `max_resets` times in `window_s` seconds; after that it
stays latched and says so, so a cable that keeps dropping out gets looked at by a person.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .safety import Safety
from .world import Hand


class RecoverySupervisor:
    def __init__(self, gate, safety: Safety, arm, announce: Callable[[str], None], *, clear_s: float = 2.0, max_resets: int = 3,
                 window_s: float = 60.0, poll_s: float = 0.1, retry_s: float = 1.0, clock=time.monotonic) -> None:
        self.gate, self.safety, self.arm, self.announce = gate, safety, arm, announce
        self.clear_s, self.max_resets, self.window_s = clear_s, max_resets, window_s
        self.poll_s, self.retry_s, self.clock = poll_s, retry_s, clock
        self._resets: list[float] = []
        self._next_try = 0.0
        self._clear_since: float | None = None
        self._recovering = False
        self._gave_up_noted = False
        self.last_error = ""
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="fault-recovery", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._shutdown.wait(self.poll_s):
            try:
                self.step()
            except Exception as exc:              # the supervisor must never take the process down; the latch simply stays
                self.last_error = f"{type(exc).__name__}: {exc}"

    def _prune(self, now: float) -> None:
        self._resets = [t for t in self._resets if now - t < self.window_s]

    def resets_left(self) -> int:
        self._prune(self.clock())
        return max(0, self.max_resets - len(self._resets))

    def gave_up(self) -> bool:
        return self.safety.stopped and self.safety.stop_kind == "fault" and self.resets_left() == 0

    def step(self) -> None:
        now = self.clock()
        if self._recovering:
            self._watch_clear(now)
            return
        if not (self.safety.stopped and self.safety.stop_kind == "fault"):
            self._gave_up_noted = False
            return
        self._prune(now)
        if len(self._resets) >= self.max_resets:
            if not self._gave_up_noted:
                self._gave_up_noted = True
                self.announce("A camera keeps dropping out; check the cable and say 'carry on'")
            return
        if now < self._next_try:
            return
        self.safety.hold(True)                     # no motion between the latch clearing and the way being seen clear
        try:
            self.gate.reset()                      # every camera must deliver fresh frames; raises if one never does (the latch then stays)
        except Exception as exc:
            self.safety.hold(False)
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._next_try = now + self.retry_s
            return
        resume = getattr(self.arm, "resume", None)
        if callable(resume):
            resume()                               # the arm's own latch; the scene (and a held object) is untouched
        self._resets.append(now)
        self._recovering, self._clear_since = True, None

    def _hands_clear(self) -> bool:
        monitor = getattr(self.gate, "monitor", None)
        if monitor is None:
            return False
        snap = monitor.snapshot()
        if snap["age_ms"] is None or not snap["healthy"]:
            return False
        hands = [Hand(**h) for h in monitor.world.snapshot()["hands"]]
        return self.safety.hands_clear(hands, self.gate.arm.path())

    def _watch_clear(self, now: float) -> None:
        if self.safety.stopped:                    # a new fault (or a person's stop) during the clear window: start over / hand over
            self.safety.hold(False)
            self._recovering, self._clear_since = False, None
            return
        if self._hands_clear():
            if self._clear_since is None:
                self._clear_since = now
            if now - self._clear_since >= self.clear_s:
                self.safety.hold(False)
                self._recovering, self._clear_since = False, None
        else:
            self._clear_since = None

    def status(self) -> dict | None:
        """For Deck: while a fault stop is being recovered from, how long until the arm is released."""
        if not (self.safety.stopped and self.safety.stop_kind == "fault") and not self._recovering:
            return None
        now = self.clock()
        if self._recovering:
            left = self.clear_s if self._clear_since is None else max(0.0, self.clear_s - (now - self._clear_since))
        elif self.gave_up():
            left = None
        else:
            left = max(0.0, self._next_try - now)
        return {"recovering_in_s": None if left is None else round(left, 1), "resets_left": self.resets_left()}
