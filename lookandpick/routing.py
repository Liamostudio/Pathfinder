"""Bounded waypoint search for the simulated arm; no I/O and no motion commands.

Waypoints exclude the supplied start and include the destination. The zone callback
must already check the tool's swept radius (as ManualZoneGuard.violation does).
Reach is checked at waypoints; the controller must still validate/monitor every
executed segment. None means no route in these candidate families, not a proof
that no arbitrary path exists. Up-and-over is preferred to lateral detours.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable

from .safety import point_segment_distance, valid_position
from .world import Hand, Vec3


def _levels(low: float, high: float, step: float = .01):
    if low > high: return
    step = max(step, (high-low)/128)
    for i in range(math.ceil((high-low)/step)+1):
        yield min(high, low+i*step)


def plan_route(start: Vec3, end: Vec3, violation: Callable[[Vec3, Vec3], str | None],
               reachable: Callable[[Vec3], bool], hands: Iterable[Hand] = (),
               hand_clearance_m: float = .10, tool_radius_m: float = .04,
               margin_m: float = .02, ceiling_m: float = .35) -> list[Vec3] | None:
    """Try straight, then the lowest clear overhead route, then shortest lateral.

    Lateral candidates use one bend (quarter/mid/three-quarter points) or two
    parallel offsets, on both sides. Search extends at most one metre sideways;
    `reachable` supplies the actual workspace boundary. Invalid geometry fails
    closed. Callback failures propagate so the caller can report the fault.
    """
    values = (hand_clearance_m, tool_radius_m, margin_m, ceiling_m)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Route clearances and ceiling must be finite, non-negative metres')
    if not valid_position(start) or not valid_position(end): return None
    start, end = tuple(start), tuple(end)
    hands = tuple(hands)
    if any(not valid_position(h.position) or not math.isfinite(h.radius_m) or h.radius_m < 0 for h in hands): return None
    if not reachable(start) or not reachable(end): return None

    def clear(a, b):
        return (violation(a, b) is None and all(
            point_segment_distance(h.position, a, b) + 1e-12 >= hand_clearance_m+h.radius_m for h in hands))

    def valid(points):
        previous = start
        for point in points:
            if not reachable(point) or not clear(previous, point): return False
            previous = point
        return True

    def compact(points):
        result, previous = [], start
        for point in points:
            point = tuple(point)
            if math.dist(previous, point) > 1e-9: result.append(point); previous = point
        return result or [end]

    if clear(start, end): return [end]
    # An obstructed endpoint cannot be fixed by changing the route to it.
    if not clear(start, start) or not clear(end, end): return None

    base = max(start[2], end[2])
    for height in _levels(base + max(.01, tool_radius_m), ceiling_m-margin_m):
        points = compact([(start[0], start[1], height), (end[0], end[1], height), end])
        if valid(points):
            # Extra margin beyond the first height accepted by the swept-radius guard.
            padded = compact([(start[0], start[1], height+margin_m), (end[0], end[1], height+margin_m), end])
            if valid(padded): return padded

    dx, dy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(dx, dy)
    # A vertical segment has no preferred horizontal side: search both XY axes.
    normals = [(-dy/length, dx/length)] if length > 1e-9 else [(1., 0.), (0., 1.)]
    extent = min(1., max(.35, length, *(hand_clearance_m+h.radius_m+tool_radius_m+margin_m for h in hands)))
    best, best_length = None, math.inf
    for nx, ny in normals:
        for sign in (-1, 1):
            # Each candidate family keeps the margin beyond its first clear offset.
            found = set()
            for offset in _levels(max(.01, tool_radius_m), extent):
                def candidates(distance):
                    ox, oy = nx*distance*sign, ny*distance*sign
                    bends = [[(start[0]+dx*t+ox, start[1]+dy*t+oy, start[2]+(end[2]-start[2])*t), end] for t in (.25, .5, .75)]
                    return bends + [[(start[0]+ox, start[1]+oy, start[2]), (end[0]+ox, end[1]+oy, end[2]), end]]
                raw, padded = candidates(offset), candidates(offset+margin_m)
                for family, points in enumerate(raw):
                    if family in found or not valid(points): continue
                    points = compact(padded[family])
                    if not valid(points): continue
                    found.add(family)
                    distance = sum(math.dist(a, b) for a, b in zip([start]+points, points))
                    if distance < best_length: best, best_length = points, distance
                if len(found) == 4: break
    return best
