"""Task 5 of docs/motion-tasks-2026-11-04.md: while a hand holds the arm up, look for a way around it. Real gate, watchdog and planner;
a slow fake arm so a hand can land mid-move."""
import threading
import time

import pytest

from lookandpick.safety import point_segment_distance
from test_hand_gate import hand_at, wait_for
from test_motion_recovery import SlowArm, run, world  # noqa: F401  (world is a fixture)

CUBE = (0.30, 0.10, 0.018)             # a long reach, so there is room for a hand in the middle of the path and a way around it


class LegArm(SlowArm):
    def __init__(self, reachable=None):
        super().__init__()
        self.legs, self._reach = [], reachable
        self.duration = 0.1                      # quick at full speed; a hand within 50 cm still slows it to a crawl, as it should

    def move_to(self, target, speed_scale):
        self.legs.append((self.position, tuple(target)))
        return super().move_to(target, speed_scale)

    def reachable(self, point):
        return True if self._reach is None else self._reach(point)


def hands_of(runtime):
    return runtime.tools.world.snapshot()["hands"]


def test_a_hand_parked_in_the_path_with_room_on_the_other_side_means_a_route_around(world):
    gate, runtime, arm, camera = world(red=CUBE, arm=LegArm())
    gate.detector.detections = [hand_at(0.15, 0.10)]                        # on the straight line, with room above and to both sides
    assert wait_for(lambda: gate.status()["hands"] >= 1)                      # seen (the idle arm itself is not yet inside the stop distance)
    thread, out = run(runtime, "pick up the red cube")
    thread.join(15)
    assert out and out[0].steps[0]["result"].startswith("picked red cube") and "(rerouted" in out[0].steps[0]["result"]
    assert "Going around" in runtime.said and "Pausing: a hand is in the way" in runtime.said
    assert gate.detector.detections and not runtime.safety.stopped            # the hand never left, and nothing latched
    hand = hands_of(runtime)[0]
    detour = runtime.tools.last_route["waypoints"]
    legs = [leg for leg in arm.legs if leg[1] in {tuple(p) for p in detour}]
    assert legs and all(point_segment_distance(tuple(hand["position"]), a, b) >= 0.10 + hand["radius_m"] - 1e-6 for a, b in legs)


def test_a_hand_in_a_narrow_workspace_means_wait_then_carry_on(world):
    corridor = lambda p: abs(p[1] - 0.10) <= 0.02 and p[2] <= 0.14           # no room beside or above: nothing to route into
    gate, runtime, arm, camera = world(red=CUBE, arm=LegArm(corridor))
    gate.detector.detections = [hand_at(0.15, 0.10)]
    assert wait_for(lambda: gate.status()["hands"] >= 1)                      # seen (the idle arm itself is not yet inside the stop distance)
    thread, out = run(runtime, "pick up the red cube")
    time.sleep(1.5)
    assert thread.is_alive() and "Going around" not in runtime.said            # it looked, found nothing, and kept waiting
    gate.detector.detections = []
    thread.join(10)
    assert out and out[0].steps[0]["result"] == "picked red cube" and "Clear, carrying on" in runtime.said


def test_a_hand_that_moves_into_the_new_route_pauses_it_as_normal(world):
    low = lambda p: p[2] <= 0.12                                             # no way over, so the detour goes sideways
    gate, runtime, arm, camera = world(red=CUBE, arm=LegArm(low))
    gate.detector.detections = [hand_at(0.15, 0.10)]
    assert wait_for(lambda: gate.status()["hands"] >= 1)                      # seen (the idle arm itself is not yet inside the stop distance)
    thread, out = run(runtime, "pick up the red cube")
    assert wait_for(lambda: "Going around" in runtime.said, 5)
    runtime.tools.hand_routes = False                                        # no second reroute: this is about the hand check on the new legs
    first = runtime.tools.last_route["waypoints"][0]
    gate.detector.detections = [hand_at(first[0], first[1])]                  # the hand steps onto the first waypoint of the new route
    time.sleep(1.0)
    assert thread.is_alive() and arm.speed == 0.0 and not runtime.safety.stopped        # the new leg is held by the hand check, like any other
    gate.detector.detections = []
    thread.join(15)
    assert out and out[0].steps[0]["result"].startswith("picked red cube") and not runtime.safety.stopped


def test_stop_during_a_reroute_stops_at_once(world):
    gate, runtime, arm, camera = world(red=CUBE, arm=LegArm())
    gate.detector.detections = [hand_at(0.15, 0.10)]
    assert wait_for(lambda: gate.status()["hands"] >= 1)                      # seen (the idle arm itself is not yet inside the stop distance)
    thread, out = run(runtime, "pick up the red cube")
    assert wait_for(lambda: "Going around" in runtime.said, 5)
    started = time.monotonic()
    runtime.stop()
    assert arm.halted and runtime.safety.stopped and time.monotonic() - started < 0.2
    time.sleep(0.5)
    assert thread.is_alive() and arm.halted                                  # held, not resumed by itself
    runtime.tools.cancel()
    thread.join(5)


def test_a_hand_that_pauses_the_move_for_more_than_a_second_and_a_half_starts_a_reroute(world):
    gate, runtime, arm, camera = world(red=(0.40, 0.10, 0.018), arm=LegArm())
    arm.duration = 1.5                                                        # a slow first leg, so a hand can arrive in the middle of it
    runtime.tools.mid_move_pause_s = 0.4                                     # the real value is 1.5 s; shortened to keep the test quick
    thread, out = run(runtime, "pick up the red cube")
    assert wait_for(lambda: arm.legs and arm.position != (0.0, 0.10, 0.05) and not arm.halted, 3)
    gate.detector.detections = [hand_at(0.22, 0.10)]                          # a hand reaches into the rest of the path, mid-move
    assert wait_for(lambda: "Going around" in runtime.said, 5)                # paused, looked, found a way, cancelled the move
    arm.duration = 0.1                                                        # (the hand's slow zone still limits the speed; just don't wait minutes)
    thread.join(20)
    assert out and out[0].steps[0]["result"].startswith("picked red cube")
    assert "Going around" in runtime.said and not runtime.safety.stopped and not arm.halted      # cancelled and re-routed, never latched
    assert gate.detector.detections                                           # and the hand was still there when it finished
