"""ManualZoneGuard: the no-go cubes a person draws in the voxel map are HARD rules for the arm.

A move whose path (with the tool's radius) passes through a manually blocked cube is refused before the arm is commanded, and the model is told why.
Deliberately narrow and checkable:
  * only MANUAL no-go cubes block. Live detections (hands, people) update the picture and the speed gate handles people, so a noisy detection can never
    randomly block a move; unknown (amber) cubes do not block either, because an RGB camera can never certify empty space and that would block everything;
  * the check is local and instant (no network, no model): it reads the active workspace's saved policy through the voxel service in this process;
  * the path is sampled every centimetre and each sample looks up the cubes it touches, so a path that only partly overlaps the working volume cannot slip
    past a blocked cube near its edge;
  * with no active workspace, no working area or no marker alignment there are no zones, and nothing is blocked.
This sits BESIDE the hand gate and the stop latch, never instead of them.
"""
from __future__ import annotations

import math
from typing import Callable

from .world import Vec3

SAMPLE_STEP_M = 0.01


def _apply(matrix, p) -> list[float]:
    return [sum(matrix[i][j] * p[j] for j in range(3)) + matrix[i][3] for i in range(3)]


class ManualZoneGuard:
    def __init__(self, source: Callable[[], tuple | None], *, tool_radius_m: float = 0.04, step_m: float = SAMPLE_STEP_M) -> None:
        """`source()` -> (grid, blocked cube ids, canonical_from_marker 4x4, canonical units per metre) or None when there are no zones."""
        self.source, self.tool_radius_m, self.step_m = source, tool_radius_m, step_m

    def violation(self, start: Vec3, end: Vec3) -> str | None:
        """None if the move may go ahead, otherwise a short reason naming the no-go cube it would cross."""
        zones = self.source()
        if zones is None:
            return None
        grid, blocked, canonical_from_marker, units = zones
        blocked = set(blocked)
        if not blocked:
            return None
        a, b = _apply(canonical_from_marker, start), _apply(canonical_from_marker, end)
        radius = self.tool_radius_m * units
        steps = max(1, math.ceil(math.dist(a, b) / max(1e-9, self.step_m * units)))
        for k in range(steps + 1):
            t = k / steps
            hit = grid.cells_at([a[i] + (b[i] - a[i]) * t for i in range(3)], radius) & blocked
            if hit:
                x, y, z = grid.coordinates(min(hit))
                return f"the path crosses a no-go zone (cube {x},{y},{z})"
        return None


def from_areas(areas, **kwargs) -> ManualZoneGuard:
    """A guard reading the ACTIVE workspace of a `workspaces.areas.WorkAreas`, live, at the moment of each move."""
    def source():
        active = areas.service.repo.active()
        if not active or not (active.get("working_area") or {}).get("enabled"):
            return None
        # The ACTIVE saved version, never the draft: an unsaved re-scan has no working area, and a guard that read it would raise on every move.
        mapping, anchor = areas.occupancy(active["id"], active.get("version_id"))
        if not anchor:
            return None
        return mapping.grid, mapping.snapshot()["manual_blocked"], anchor["canonical_from_marker"], anchor["canonical_units_per_metre"]
    return ManualZoneGuard(source, **kwargs)
