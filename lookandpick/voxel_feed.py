"""VoxelFeed: what the safety camera sees, shown live in the voxel map.

    hand gate (camera -> MediaPipe -> hand positions in the marker frame) --> voxel service --> red cubes in the visualiser

It reads the hand positions the hand gate already computes and offers them to the voxel service as `hand` observations, about ten times a
second. That is all it does. It runs on its own thread, only READS the gate's hands, and can never touch the arm, the stop latch or the
watchdog: if it fails, the safety path is unaffected and the map simply stops updating (the cubes then expire and go amber, "unknown").
The voxel map is advisory. A cube a hand has been near turns red; no cube is ever made green from a camera detection.

It needs the active workspace's voxel revision and marker-alignment id each time (they change when the working area is edited), so both are
looked up per tick rather than cached.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .world import Hand

MAX_RADIUS_M = 0.25            # the voxel service's limit for one observation


class VoxelFeed:
    def __init__(self, current: Callable[[], tuple | None], observe: Callable[[str, dict], object], hands: Callable[[], list[Hand]], *,
                 source: str = "hand-gate", hz: float = 10.0, clock=time.time) -> None:
        """`current()` -> (workspace id, voxel revision, calibration id) or None when nothing is active;
        `observe(id, payload)` offers one observation (it may raise ValueError); `hands()` -> the gate's current hands."""
        if not 0 < hz <= 30:
            raise ValueError("hz must be in (0, 30]")
        self.current, self.observe, self.hands, self.source, self.period, self._clock = current, observe, hands, source, 1 / hz, clock
        self._sequence = 0
        self._stop, self._thread = threading.Event(), None
        self.sent = self.errors = 0
        self.last_error: str | None = None
        self.last_ok: float | None = None

    def tick(self) -> int:
        """Offer the current hands once. Returns how many observations were accepted; never raises."""
        try:
            hands = [h for h in self.hands() if all(abs(c) < 1e6 for c in h.position)]
            target = self.current()
            if not hands or target is None:
                return 0
            key, revision, calibration = target
            accepted = 0
            for hand in hands:
                payload = {"mode": "points", "kind": "hand", "frame": "marker_metres", "points": [list(hand.position)],
                           "radius": max(0.0, min(float(hand.radius_m), MAX_RADIUS_M)), "revision": revision, "sequence": self._sequence,
                           "source": self.source, "captured_at_ms": int(self._clock() * 1000), "calibration_id": calibration}
                self._sequence += 1
                self.observe(key, payload)
                accepted += 1
            self.sent += accepted
            self.last_ok = self._clock()
            return accepted
        except Exception as exc:                          # a feed problem must never reach the safety path
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return 0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the voxel feed is already running")
        self._thread = threading.Thread(target=self._run, name="voxel-feed", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.period):
            self.tick()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def status(self) -> dict:
        return {"running": self._thread is not None and self._thread.is_alive() and not self._stop.is_set(), "source": self.source,
                "sent": self.sent, "errors": self.errors, "last_error": self.last_error, "last_ok": self.last_ok}


def for_workspaces(areas, hands: Callable[[], list[Hand]], **kwargs) -> VoxelFeed:
    """A feed that targets the ACTIVE workspace of a `workspaces.areas.WorkAreas` (the one saved and activated in Deck)."""
    def current():
        active = areas.service.repo.active()
        if not active or not active.get("working_area") or not (active.get("working_area") or {}).get("enabled"):
            return None
        mapping, anchor = areas.occupancy(active["id"])
        return (active["id"], mapping.revision, anchor["id"]) if anchor else None
    return VoxelFeed(current, areas.observe, hands, **kwargs)


def gate_hands(gate) -> Callable[[], list[Hand]]:
    """The hands the hand gate's monitor currently holds, read-only (no tick, no side effects)."""
    def hands():
        monitor = gate.monitor
        return [] if monitor is None else [Hand(**h) for h in monitor.world.snapshot()["hands"]]
    return hands
