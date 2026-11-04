"""Local, network-free safety layer: speed-and-separation plus a stop latch."""
from __future__ import annotations

import math
import threading

from .world import Hand, Vec3

STOP_DISTANCE_M = 0.15  # at or inside this the arm halts (real arm: placeholder until measured on the bench)
SLOW_DISTANCE_M = 0.50  # outside this the arm runs at full speed
NO_GATE_ID = "hand-gate-not-running"  # the fail-closed stand-in hand used when the gate is down: a refusal, never something to wait out
SIM_STOP_DISTANCE_M = 0.10  # the simulated arm only: lets a route 10 cm from a hand pass without the hand check stopping it


def distance(a: Vec3, b: Vec3) -> float:
    return math.dist(a, b)


def point_segment_distance(p: Vec3, a: Vec3, b: Vec3) -> float:
    ab = [b[i] - a[i] for i in range(3)]
    denom = sum(c * c for c in ab)
    if denom == 0:
        return distance(p, a)
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / denom))
    return distance(p, tuple(a[i] + t * ab[i] for i in range(3)))


def speed_scale(separation_m: float, stop_m: float = STOP_DISTANCE_M, slow_m: float = SLOW_DISTANCE_M) -> float:
    """0.0 (stop) to 1.0 (full speed), linear between the stop and slow distances."""
    if not math.isfinite(separation_m):
        return 0.0
    if separation_m <= stop_m:
        return 0.0
    if separation_m >= slow_m:
        return 1.0
    return (separation_m - stop_m) / (slow_m - stop_m)


def valid_position(position: Vec3) -> bool:
    """Reject malformed or non-finite coordinates before planning any motion."""
    try:
        return len(position) == 3 and all(math.isfinite(c) for c in position)
    except (TypeError, ValueError, OverflowError):
        return False


class Safety:
    def __init__(self, stop_distance_m: float = STOP_DISTANCE_M, slow_distance_m: float = SLOW_DISTANCE_M) -> None:
        if not (math.isfinite(stop_distance_m) and math.isfinite(slow_distance_m) and 0 <= stop_distance_m < slow_distance_m):
            raise ValueError("need 0 <= stop distance < slow distance")
        self.stop_distance_m, self.slow_distance_m = stop_distance_m, slow_distance_m
        self._stopped = threading.Event()

    @classmethod
    def for_arm(cls, arm) -> "Safety":
        """The simulated arm (it says `simulated = True`) stops at 10 cm; every other arm, the real one included, keeps 15 cm."""
        return cls(stop_distance_m=SIM_STOP_DISTANCE_M) if getattr(arm, "simulated", False) else cls()

    def stop(self) -> None:
        """Latches. Bypasses every other check."""
        self._stopped.set()

    def reset(self) -> None:
        self._stopped.clear()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def allowed_speed(self, hands: list[Hand], path: tuple[Vec3, Vec3]) -> float:
        """Speed scale for a move along `path` given where the hands are right now."""
        if self.stopped:
            return 0.0
        if not all(valid_position(p) for p in path):
            return 0.0
        if not all(valid_position(h.position) for h in hands):
            return 0.0
        if not all(math.isfinite(h.radius_m) and h.radius_m >= 0 for h in hands):
            return 0.0
        if not hands:
            return 1.0
        nearest = min(max(0.0, point_segment_distance(h.position, *path) - h.radius_m) for h in hands)
        return speed_scale(nearest, self.stop_distance_m, self.slow_distance_m)

    def hand_in_the_way(self, hands: list[Hand], path: tuple[Vec3, Vec3]) -> bool:
        """True only when the speed is zero SOLELY because a hand is too close: not stopped, and every input valid.
        That is the one zero worth waiting out; any other zero (a latch, bad geometry) is a refusal."""
        if self.stopped or not all(valid_position(p) for p in path) or not all(valid_position(h.position) for h in hands):
            return False
        if not all(math.isfinite(h.radius_m) and h.radius_m >= 0 for h in hands):
            return False
        if any(h.id == NO_GATE_ID for h in hands):
            return False
        return bool(hands) and self.allowed_speed(hands, path) <= 0.0
