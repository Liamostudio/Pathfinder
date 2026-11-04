"""Task 4 of docs/motion-tasks-2026-11-04.md: route around no-go cubes. Real grids and ManualZoneGuard, real Tools, Codex's planner; fake arm."""
import math

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import TrackedObject, WorldState
from lookandpick.workspaces.voxels import Grid
from lookandpick.zones import ManualZoneGuard

AREA = {"enabled": True, "centre": [0.0, 0.0], "width": 0.8, "depth": 0.8, "height": 0.6, "base": 0.0, "yaw": 0.0, "cell_size": 0.05,
        "ttl_ms": 500, "hold_ms": 750, "margin": 0.0}
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def guard_for(predicate):
    grid = Grid(AREA)
    blocked = {i for i in range(grid.count) if predicate(grid.centre(i))}
    return ManualZoneGuard(lambda: (grid, blocked, IDENTITY, 1.0), tool_radius_m=0.04)


class RecordingArm(SimArm):
    def __init__(self):
        super().__init__(start=(-0.25, 0.0, 0.2))
        self.legs = []

    def move_to(self, target, speed_scale):
        self.legs.append((self.position, tuple(target)))
        return super().move_to(target, speed_scale)


def rig(guard, red=(-0.25, 0.0, 0.018)):
    said = []
    arm = RecordingArm()
    tools = Tools(WorldState(), FakeTracker([TrackedObject("obj1", "red cube", red)]), arm, Safety.for_arm(arm), say=said.append, announce=said.append, zones=guard)
    return tools, arm, said


def every_leg_is_clear(arm, guard):
    return all(guard.violation(a, b) is None for a, b in arm.legs)


def test_a_wall_of_cubes_between_the_pick_and_the_drop_means_the_arm_goes_over():
    guard = guard_for(lambda p: abs(p[0]) < 0.03 and p[2] < 0.15)              # a low wall across the whole width
    tools, arm, said = rig(guard)
    assert tools.pick("obj1") == "picked red cube"
    out = tools.place("obj1", (0.25, 0.0, 0.018))
    assert out == "placed red cube (rerouted over a no-go zone)"
    assert tools.last_route["kind"] == "over" and max(p[2] for p in tools.last_route["waypoints"]) > 0.15
    assert "Going around" in said and every_leg_is_clear(arm, guard)


def test_a_tall_but_narrow_wall_means_around():
    guard = guard_for(lambda p: abs(p[0]) < 0.03 and abs(p[1]) < 0.06)         # tall (the whole height) but only 12 cm wide
    tools, arm, said = rig(guard)
    assert tools.pick("obj1") == "picked red cube"
    out = tools.place("obj1", (0.25, 0.0, 0.018))
    assert out == "placed red cube (rerouted around a no-go zone)"
    assert tools.last_route["kind"] == "around" and any(abs(p[1]) > 0.10 for p in tools.last_route["waypoints"])
    assert every_leg_is_clear(arm, guard)


def test_boxed_in_gives_an_explanation_gives_up_and_leaves_the_arm_where_it_was():
    guard = guard_for(lambda p: abs(p[0]) < 0.03)                              # a wall right across and up to the ceiling
    tools, arm, said = rig(guard)
    assert tools.pick("obj1") == "picked red cube"
    before = arm.position
    out = tools.place("obj1", (0.25, 0.0, 0.018))
    assert out.startswith("blocked: the path crosses a no-go zone") and "no way around it was found" in out
    assert tools.gave_up and "I can't find a way past the no-go zone; move the target or the zone and I'll try again." in said
    assert arm.position == before


def test_a_clear_path_is_not_rerouted_and_says_nothing_about_it():
    guard = guard_for(lambda p: abs(p[0]) < 0.03 and abs(p[1]) > 0.30)         # a block well off to the side
    tools, arm, said = rig(guard)
    assert tools.pick("obj1") == "picked red cube" and tools.place("obj1", (0.25, 0.0, 0.018)) == "placed red cube"
    assert "Going around" not in said and tools.last_route is None


def test_every_leg_of_a_detour_still_goes_through_the_hand_check():
    guard = guard_for(lambda p: abs(p[0]) < 0.03 and p[2] < 0.15)
    tools, arm, said = rig(guard)
    assert tools.pick("obj1") == "picked red cube"
    from lookandpick.world import Hand
    checked = []
    original = tools.safety.allowed_speed

    def spy(hands, path):
        checked.append(path)
        return original(hands, path)
    tools.safety.allowed_speed = spy
    assert tools.place("obj1", (0.25, 0.0, 0.018)).startswith("placed red cube (rerouted")
    for a, b in arm.legs[-4:]:
        assert (a, b) in checked                                               # each executed leg was speed-checked before it was commanded


def test_the_move_to_rest_uses_the_same_routing():
    guard = guard_for(lambda p: abs(p[0]) < 0.03 and p[2] < 0.15 and p[0] > -0.4)
    tools, arm, said = rig(guard)
    arm.position = (-0.30, 0.0, 0.2)
    arm.rest_position = (0.30, 0.0, 0.2)                                       # Rest is on the far side of a low wall, at a height that clears it
    guard2 = guard_for(lambda p: abs(p[0]) < 0.03 and p[2] < 0.25)
    tools.zones = guard2
    assert tools.go_rest() == "at rest"
    assert "Going around" in said and arm.position == (0.30, 0.0, 0.2) and arm.legs
