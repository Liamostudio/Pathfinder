"""Behaviour at the tracker/controller boundary, using simulated inputs only."""
from dataclasses import replace
import threading

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import Hand, TrackedObject, WorldState


class FreshTracker(FakeTracker):
    def objects(self):
        return [replace(obj) for obj in super().objects()]


def setup(tracker=None, arm=None):
    tracker = tracker if tracker is not None else FreshTracker()
    arm = arm if arm is not None else SimArm()
    safety = Safety()
    return tracker, arm, safety, Tools(WorldState(), tracker, arm, safety)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_invalid_hand_or_path_blocks_motion(bad):
    safety = Safety()
    assert safety.allowed_speed([Hand('h', (bad, 0, 0))], ((0, 0, 0), (1, 0, 0))) == 0
    assert safety.allowed_speed([], ((0, 0, 0), (bad, 0, 0))) == 0


def test_pick_place_survives_new_detection_instances():
    tracker, arm, _, tools = setup()
    assert tools.pick('obj1').startswith('picked')
    assert next(o for o in tools.get_scene()['objects'] if o['id'] == 'obj1')['held']
    assert tools.place('obj1', (0.2, -0.1, 0)).startswith('placed')
    assert not arm.gripping
    assert all(not o.held for o in tracker.objects())


def test_held_object_survives_disappearing_from_camera():
    tracker, _, _, tools = setup()
    tools.pick('obj1')
    tracker._objects = []
    assert tools.place('obj1', (0.2, -0.1, 0)).startswith('placed')


def test_cannot_pick_another_object_while_holding_one():
    _, arm, _, tools = setup()
    tools.pick('obj1')
    before = list(arm.log)
    assert tools.pick('obj2').startswith('error')
    assert arm.log == before


def test_retreat_block_reports_partial_pick_and_keeps_held_state():
    tracker, arm, _, tools = setup()
    original_grip = arm.grip

    def grip_then_hand_appears(closed):
        result = original_grip(closed)
        tracker.set_hands([Hand('h', arm.position)])
        return result

    arm.grip = grip_then_hand_appears
    assert tools.pick('obj1').startswith('blocked')
    assert arm.gripping
    assert next(o for o in tools.world.snapshot()['objects'] if o['id'] == 'obj1')['held']


def test_retreat_block_reports_partial_place_and_released_state():
    tracker, arm, _, tools = setup()
    tools.pick('obj1')
    original_grip = arm.grip

    def release_then_hand_appears(closed):
        result = original_grip(closed)
        tracker.set_hands([Hand('h', arm.position)])
        return result

    arm.grip = release_then_hand_appears
    assert tools.place('obj1', (0.2, -0.1, 0)).startswith('blocked')
    assert not arm.gripping
    assert not any(o['held'] for o in tools.world.snapshot()['objects'])


def test_stop_after_descent_prevents_grip():
    _, arm, safety, tools = setup()
    original_move = arm.move_to

    def move_then_stop(target, scale):
        result = original_move(target, scale)
        if target[2] == 0:
            safety.stop()
        return result

    arm.move_to = move_then_stop
    assert tools.pick('obj1').startswith('blocked')
    assert not arm.gripping


def test_arm_refusal_is_not_reported_as_success():
    _, arm, _, tools = setup()
    arm.halt()
    assert tools.pick('obj1').startswith('blocked')
    assert not arm.gripping


def test_failed_gripper_does_not_mark_object_held():
    _, arm, _, tools = setup()
    arm.grip = lambda closed: False
    assert tools.pick('obj1').startswith('blocked')
    assert not any(o['held'] for o in tools.world.snapshot()['objects'])


def test_empty_fake_scene_stays_empty():
    assert FakeTracker(objects=[]).objects() == []


def test_world_owns_detection_data():
    obj = TrackedObject('x', 'cube', (0, 0, 0))
    world = WorldState()
    world.update(objects=[obj])
    obj.position = (1, 1, 1)
    assert world.snapshot()['objects'][0]['position'] == (0, 0, 0)


def test_concurrent_pick_is_blocked_but_stop_does_not_wait():
    _, arm, _, tools = setup()
    entered, resume = threading.Event(), threading.Event()
    original_move = arm.move_to
    results = []

    def slow_move(target, scale):
        entered.set()
        assert resume.wait(2)
        return original_move(target, scale)

    arm.move_to = slow_move
    worker = threading.Thread(target=lambda: results.append(tools.pick('obj1')))
    worker.start()
    try:
        assert entered.wait(2)
        assert tools.pick('obj2') == 'blocked: another action is in progress'
        assert tools.stop() == 'stopped'
        assert arm.halted
    finally:
        resume.set()
        worker.join(2)
    assert not worker.is_alive()
    assert results[0].startswith('blocked')
    assert not arm.gripping
