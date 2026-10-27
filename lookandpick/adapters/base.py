"""Adapter interfaces. Each is stubbed with fake data first, then swapped for the real thing."""
from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass

from ..world import Hand, TrackedObject, Vec3


@dataclass(frozen=True)
class PixelHand:
    """A detector's image-space landmarks in pixels, not estimated metric depth."""

    id: str
    pixels: tuple[tuple[float, float], ...]


class HandDetector(Protocol):
    def detect(self, frame: object, timestamp_ms: int) -> list[PixelHand]: ...
    def close(self) -> None: ...


class Tracker(Protocol):
    """SAM 3.1 + hand detector in production."""

    def objects(self) -> list[TrackedObject]: ...
    def hands(self) -> list[Hand]: ...


class Arm(Protocol):
    """Sim arm first; the real SO-101 only with Liam present and the 6V kill switch in reach."""

    position: Vec3

    # True means completed; False means refused/interrupted. Never just acknowledge
    # queuing here: Tools sequences the next action based on this result.
    def move_to(self, target: Vec3, speed_scale: float) -> bool: ...
    def grip(self, closed: bool) -> bool: ...
    def halt(self) -> None: ...


class SpeedControlledArm(Arm, Protocol):
    """Zero must pause ongoing motion; halt must latch independently of this setter."""

    def set_speed_scale(self, scale: float) -> None: ...
