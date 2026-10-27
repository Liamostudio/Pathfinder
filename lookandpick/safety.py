"""Local, network-free safety layer: speed-and-separation plus a stop latch."""
from __future__ import annotations

import math
import threading

from .world import Hand, Vec3

STOP_DISTANCE_M = 0.15  # at or inside this the arm halts
SLOW_DISTANCE_M = 0.50  # outside this the arm runs at full speed


def distance(a: Vec3, b: Vec3) -> float:
    return math.dist(a, b)


def point_segment_distance(p: Vec3, a: Vec3, b: Vec3) -> float:
    ab = [b[i] - a[i] for i in range(3)]
    denom = sum(c * c for c in ab)
    if denom == 0:
        return distance(p, a)
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / denom))
    return distance(p, tuple(a[i] + t * ab[i] for i in range(3)))


def speed_scale(separation_m: float) -> float:
    """0.0 (stop) to 1.0 (full speed), linear between the stop and slow distances."""
    if not math.isfinite(separation_m):
        return 0.0
    if separation_m <= STOP_DISTANCE_M:
        return 0.0
    if separation_m >= SLOW_DISTANCE_M:
        return 1.0
    return (separation_m - STOP_DISTANCE_M) / (SLOW_DISTANCE_M - STOP_DISTANCE_M)


def valid_position(position: Vec3) -> bool:
    """Reject malformed or non-finite coordinates before planning any motion."""
    try:
        return len(position) == 3 and all(math.isfinite(c) for c in position)
    except (TypeError, ValueError, OverflowError):
        return False


class Safety:
    def __init__(self) -> None:
        self._stopped = threading.Event()

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
        return speed_scale(nearest)
