"""ManualZoneGuard: the no-go cubes a person draws are hard rules for the arm. Real grid and occupancy, real Tools; fake arm."""
import pytest

from lookandpick.adapters.fake import FakeTracker
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import TrackedObject, WorldState
from lookandpick.workspaces.voxels import Grid, Occupancy
from lookandpick.zones import ManualZoneGuard

AREA = {"enabled": True, "centre": [0.0, 0.0], "width": 0.6, "depth": 0.6, "height": 0.4, "base": 0.0, "yaw": 0.0, "cell_size": 0.05,
        "ttl_ms": 500, "hold_ms": 750, "margin": 0.0}
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def cubes_around(grid, point, radius=0.0):
    return sorted(grid.cells_at(point, radius))


@pytest.fixture
def zone():
    """A no-go block of cubes around (0.10, 0.0, 0.05), and a guard reading it."""
    mapping = Occupancy(Grid(AREA), "rev")
    mapping.manual_blocked = set(cubes_around(mapping.grid, [0.10, 0.0, 0.05], 0.03))
    guard = ManualZoneGuard(lambda: (mapping.grid, mapping.snapshot()["manual_blocked"], IDENTITY, 1.0), tool_radius_m=0.02)
    return guard, mapping


def test_a_move_through_a_blocked_cube_is_refused_with_the_reason_and_a_clear_move_is_allowed(zone):
    guard, _ = zone
    reason = guard.violation((-0.1, 0.0, 0.05), (0.25, 0.0, 0.05))
    assert reason and "no-go zone" in reason
    assert guard.violation((-0.1, 0.20, 0.05), (0.25, 0.20, 0.05)) is None             # a parallel path well clear of it
    assert guard.violation((-0.1, 0.0, 0.30), (0.25, 0.0, 0.30)) is None               # over the top of it


def test_the_tool_radius_matters_a_path_that_grazes_the_block_is_refused(zone):
    guard, mapping = zone
    # the block spans y -5 cm to +5 cm; a path at y = +9 cm with a 2 cm tool just clears it, but the same path with a 9 cm tool does not
    assert guard.violation((-0.1, 0.09, 0.05), (0.25, 0.09, 0.05)) is None
    wide = ManualZoneGuard(lambda: (mapping.grid, mapping.snapshot()["manual_blocked"], IDENTITY, 1.0), tool_radius_m=0.09)
    assert wide.violation((-0.1, 0.09, 0.05), (0.25, 0.09, 0.05)) is not None


def test_a_path_that_starts_or_ends_outside_the_volume_cannot_slip_past_a_block_near_the_edge():
    mapping = Occupancy(Grid(AREA), "rev")
    mapping.manual_blocked = set(cubes_around(mapping.grid, [0.27, 0.0, 0.05], 0.02))      # a block at the +x edge of the 0.6 m volume
    guard = ManualZoneGuard(lambda: (mapping.grid, mapping.snapshot()["manual_blocked"], IDENTITY, 1.0), tool_radius_m=0.02)
    assert guard.violation((0.0, 0.0, 0.05), (0.60, 0.0, 0.05)) is not None                # ends 30 cm outside the volume, crosses the block on the way


def test_no_zones_means_nothing_is_blocked():
    for source in (lambda: None, lambda: (Grid(AREA), [], IDENTITY, 1.0)):
        assert ManualZoneGuard(source).violation((0, 0, 0.05), (0.2, 0, 0.05)) is None


def test_the_marker_to_scene_transform_and_scale_are_applied():
    mapping = Occupancy(Grid(AREA), "rev")
    mapping.manual_blocked = set(cubes_around(mapping.grid, [0.20, 0.0, 0.05], 0.02))      # in SCENE units
    scale2 = [[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]]                       # the scene has 2 units per metre
    guard = ManualZoneGuard(lambda: (mapping.grid, mapping.snapshot()["manual_blocked"], scale2, 2.0), tool_radius_m=0.01)
    assert guard.violation((0.0, 0.0, 0.025), (0.2, 0.0, 0.025)) is not None               # 0.1 m in the world is 0.2 in the scene
    assert guard.violation((0.0, 0.2, 0.025), (0.2, 0.2, 0.025)) is None


class Arm:
    def __init__(self):
        self.position, self.moves = (0.0, 0.0, 0.05), []

    def move_to(self, target, scale):
        self.moves.append(tuple(target))
        self.position = tuple(target)
        return True

    def grip(self, closed):
        return True

    def halt(self):
        pass


def tools_for(guard, arm=None):
    arm = arm or Arm()
    tracker = FakeTracker([TrackedObject("red", "red cube", (0.30, 0.0, 0.018))])
    return Tools(WorldState(), tracker, arm, Safety(), say=lambda _: None, zones=guard), arm


def test_pick_is_refused_before_the_arm_moves_and_the_model_is_told_why(zone):
    guard, _ = zone
    tools, arm = tools_for(guard)
    result = tools.pick("red")
    assert result.startswith("blocked: the path crosses a no-go zone") and arm.moves == []


def test_the_same_pick_goes_ahead_when_the_zone_is_cleared_or_absent(zone):
    guard, mapping = zone
    mapping.manual_blocked = set()
    tools, arm = tools_for(guard)
    assert tools.pick("red") == "picked red cube" and arm.moves
    tools2, arm2 = tools_for(None)
    assert tools2.pick("red") == "picked red cube"


def test_a_zone_drawn_after_the_first_move_blocks_the_next_one(zone):
    guard, mapping = zone
    blocked = set(mapping.manual_blocked)
    mapping.manual_blocked = set()
    tools, arm = tools_for(guard)
    assert tools.pick("red") == "picked red cube"
    mapping.manual_blocked = blocked                                                          # a person draws the zone while the cube is held
    result = tools.place("red", (-0.1, 0.0, 0.018))
    assert result.startswith("blocked: the path crosses a no-go zone")


def test_the_zone_check_does_not_replace_the_stop_latch_or_hand_safety(zone):
    guard, mapping = zone
    mapping.manual_blocked = set()
    tools, arm = tools_for(guard)
    tools.safety.stop()
    assert tools.pick("red") == "blocked: stopped" and arm.moves == []


def test_the_workspace_lookup_reads_the_active_workspace_and_returns_no_zones_when_there_is_none():
    from types import SimpleNamespace

    from lookandpick.zones import from_areas
    mapping = Occupancy(Grid(AREA), "rev")
    mapping.manual_blocked = set(cubes_around(mapping.grid, [0.10, 0.0, 0.05], 0.03))
    anchor = {"id": "a", "canonical_from_marker": IDENTITY, "canonical_units_per_metre": 1.0}
    state = {"active": {"id": "ws", "working_area": AREA}}
    areas = SimpleNamespace(service=SimpleNamespace(repo=SimpleNamespace(active=lambda: state["active"])), occupancy=lambda key: (mapping, anchor))
    guard = from_areas(areas, tool_radius_m=0.02)
    assert guard.violation((-0.1, 0.0, 0.05), (0.25, 0.0, 0.05)) is not None
    state["active"] = None
    assert guard.violation((-0.1, 0.0, 0.05), (0.25, 0.0, 0.05)) is None                  # nothing active: nothing blocked
    state["active"] = {"id": "ws", "working_area": {**AREA, "enabled": False}}
    assert guard.violation((-0.1, 0.0, 0.05), (0.25, 0.0, 0.05)) is None                  # the working area is switched off
    areas.occupancy = lambda key: (mapping, None)
    state["active"] = {"id": "ws", "working_area": AREA}
    assert guard.violation((-0.1, 0.0, 0.05), (0.25, 0.0, 0.05)) is None                  # no marker alignment: the frames cannot be related
