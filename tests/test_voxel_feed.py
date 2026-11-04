"""VoxelFeed: hand positions into the voxel map, tested against the REAL occupancy code, and unable to disturb the safety path."""
import time

import pytest

from lookandpick.voxel_feed import MAX_RADIUS_M, VoxelFeed, gate_hands
from lookandpick.workspaces.voxels import Grid, Occupancy
from lookandpick.world import Hand

AREA = {"enabled": True, "centre": [0.0, 0.0], "width": 0.4, "depth": 0.4, "height": 0.3, "base": 0.0, "yaw": 0.0, "cell_size": 0.05,
        "ttl_ms": 500, "hold_ms": 750, "margin": 0.0}
ALIGNMENT = {"id": "aruco-test", "canonical_from_marker": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
             "canonical_units_per_metre": 1.0, "status": "marker_observed_visual_alignment", "metric_inputs_complete": False}


@pytest.fixture
def world():
    """A real occupancy map plus the feed's two lookups, standing in for the workspace service."""
    mapping = Occupancy(Grid(AREA), "rev")
    return mapping, (lambda: ("ws-1", mapping.revision, ALIGNMENT["id"])), (lambda key, payload: mapping.observe(payload, ALIGNMENT))


def feed_for(world, hands, **kw):
    mapping, current, observe = world
    return VoxelFeed(current, observe, lambda: list(hands), **kw), mapping


def state_at(mapping, x, y, z):
    """The state of the cube holding a marker-frame point (the test alignment is the identity)."""
    return mapping.snapshot()["states"][next(iter(mapping.grid.cells_at([x, y, z])))]


def test_a_hand_in_the_marker_frame_turns_the_matching_cube_red_and_the_far_corner_stays_unknown(world):
    feed, mapping = feed_for(world, [Hand("hand-0", (0.05, -0.05, 0.08), 0.03)])
    assert feed.tick() == 1
    assert state_at(mapping, 0.05, -0.05, 0.08) == "occupied"
    assert state_at(mapping, -0.18, 0.18, 0.25) == "unknown"                     # far from the hand
    counts = mapping.snapshot()["counts"]
    assert counts["free"] == 0 and counts["unknown"] + counts["occupied"] == mapping.grid.count     # a camera detection never makes anything green


def test_no_hands_or_no_active_workspace_sends_nothing(world):
    feed, mapping = feed_for(world, [])
    assert feed.tick() == 0 and mapping.snapshot()["counts"]["occupied"] == 0
    feed2 = VoxelFeed(lambda: None, world[2], lambda: [Hand("h", (0, 0, 0.08), 0.03)])
    assert feed2.tick() == 0 and feed2.errors == 0


def test_sequence_increases_and_each_hand_gets_its_own_radius_clamped_to_the_services_limit(world):
    seen = []
    mapping, current, observe = world
    feed = VoxelFeed(current, lambda key, payload: (seen.append(payload), observe(key, payload)),
                     lambda: [Hand("a", (0.0, 0.0, 0.08), 0.05), Hand("b", (0.1, 0.1, 0.08), 0.9)])
    feed.tick()
    feed.tick()
    assert [p["sequence"] for p in seen] == [0, 1, 2, 3]
    assert [p["radius"] for p in seen[:2]] == [0.05, MAX_RADIUS_M]
    assert {p["kind"] for p in seen} == {"hand"} and {p["frame"] for p in seen} == {"marker_metres"} and seen[0]["calibration_id"] == "aruco-test"
    assert abs(seen[0]["captured_at_ms"] - time.time() * 1000) < 1000


def test_cubes_expire_back_to_unknown_when_the_hand_leaves(world):
    now = [100.0]
    mapping = Occupancy(Grid(AREA), "rev", lambda: now[0])
    feed = VoxelFeed(lambda: ("ws", mapping.revision, "aruco-test"), lambda k, p: mapping.observe(p, ALIGNMENT),
                     lambda: [Hand("h", (0.0, 0.0, 0.08), 0.03)])
    feed.tick()
    assert mapping.snapshot()["counts"]["occupied"] >= 1
    now[0] += 5.0                                                              # the hand left; nothing is sent; the hold runs out
    assert mapping.snapshot()["counts"]["occupied"] == 0


def test_a_rejected_observation_is_counted_not_raised(world):
    def refuse(key, payload):
        raise ValueError("Working area changed; fetch the current voxel revision")
    feed = VoxelFeed(world[1], refuse, lambda: [Hand("h", (0, 0, 0.08), 0.03)])
    assert feed.tick() == 0 and feed.errors == 1 and "Working area changed" in feed.last_error
    feed2 = VoxelFeed(world[1], world[2], lambda: (_ for _ in ()).throw(RuntimeError("gate exploded")))
    assert feed2.tick() == 0 and feed2.errors == 1                             # even a broken hands source cannot raise out of the feed


def test_the_feed_thread_runs_and_stops_and_does_not_need_the_safety_objects(world):
    feed, mapping = feed_for(world, [Hand("h", (0.0, 0.0, 0.08), 0.03)], hz=30.0)
    feed.start()
    end = time.monotonic() + 2
    while feed.sent < 3 and time.monotonic() < end:
        time.sleep(0.02)
    assert feed.sent >= 3 and feed.status()["running"]
    feed.close()
    assert not feed.status()["running"]
    with pytest.raises(ValueError):
        VoxelFeed(world[1], world[2], lambda: [], hz=0)


def test_gate_hands_reads_the_monitor_without_ticking_it_and_is_empty_when_the_gate_is_not_running():
    class World:
        def snapshot(self):
            return {"hands": [{"id": "h", "position": (0.1, 0.0, 0.08), "radius_m": 0.04}]}

    class Monitor:
        world = World()

        def tick(self):
            raise AssertionError("the feed must not drive the safety monitor")
    class Gate:
        monitor = Monitor()
    assert gate_hands(Gate())() == [Hand("h", (0.1, 0.0, 0.08), 0.04)]
    Gate.monitor = None
    assert gate_hands(Gate())() == []


def test_the_workspace_target_follows_the_active_saved_version_not_the_draft():
    """The draft of an unsaved re-scan has no working area; the live feed must read and write the ACTIVE saved version."""
    from types import SimpleNamespace

    from lookandpick.voxel_feed import workspace_target
    seen = []
    anchor = {"id": "anchor-1"}
    mapping = SimpleNamespace(revision="r1")

    def occupancy(key, version=None):
        if version is None:
            raise ValueError("Draw and enable a working area first")
        return mapping, anchor
    areas = SimpleNamespace(service=SimpleNamespace(repo=SimpleNamespace(active=lambda: {"id": "ws", "version_id": "v0003", "working_area": {"enabled": True}})),
                            occupancy=occupancy, observe=lambda key, payload, version=None: seen.append((key, version)) or {"ok": True})
    current, observe = workspace_target(areas)
    assert current() == ("ws", "r1", "anchor-1")
    assert observe("ws", {"mode": "points"}) == {"ok": True} and seen == [("ws", "v0003")]
