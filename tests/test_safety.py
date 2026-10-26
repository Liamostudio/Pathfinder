from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.safety import Safety, SLOW_DISTANCE_M, STOP_DISTANCE_M, speed_scale
from lookandpick.tools import Tools
from lookandpick.world import Hand, WorldState


def make():
    tracker, arm, safety = FakeTracker(), SimArm(), Safety()
    return tracker, arm, safety, Tools(WorldState(), tracker, arm, safety, say=lambda _: None)


def test_speed_scale_bounds():
    assert speed_scale(STOP_DISTANCE_M) == 0.0
    assert speed_scale(SLOW_DISTANCE_M) == 1.0
    assert 0.0 < speed_scale((STOP_DISTANCE_M + SLOW_DISTANCE_M) / 2) < 1.0


def test_pick_with_no_hands():
    _, arm, _, tools = make()
    assert tools.pick("obj1") == "picked red cube"
    assert arm.gripping


def test_hand_near_target_blocks_motion():
    tracker, arm, _, tools = make()
    tracker.set_hands([Hand("h", (0.25, 0.10, 0.02))])
    assert tools.pick("obj1").startswith("blocked")
    assert not arm.gripping


def test_stop_latches_and_bypasses():
    _, arm, safety, tools = make()
    tools.stop()
    assert safety.stopped and arm.halted
    assert tools.pick("obj1").startswith("blocked")


def test_unknown_object():
    assert make()[3].pick("nope").startswith("error")
