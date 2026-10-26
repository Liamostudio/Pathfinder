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
    if separation_m <= STOP_DISTANCE_M:
        return 0.0
    if separation_m >= SLOW_DISTANCE_M:
        return 1.0
    return (separation_m - STOP_DISTANCE_M) / (SLOW_DISTANCE_M - STOP_DISTANCE_M)


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
        if not hands:
            return 1.0
        nearest = min(point_segment_distance(h.position, *path) for h in hands)
        return speed_scale(nearest)
