"""Adapter interfaces. Each is stubbed with fake data first, then swapped for the real thing."""
from __future__ import annotations

from typing import Protocol

from ..world import Hand, TrackedObject, Vec3


class Tracker(Protocol):
    """SAM 3.1 + hand detector in production."""

    def objects(self) -> list[TrackedObject]: ...
    def hands(self) -> list[Hand]: ...


class Arm(Protocol):
    """Sim arm first; the real SO-101 only with Liam present and the 6V kill switch in reach."""

    position: Vec3

    def move_to(self, target: Vec3, speed_scale: float) -> None: ...
    def grip(self, closed: bool) -> None: ...
    def halt(self) -> None: ...
