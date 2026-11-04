"""Task 2 of docs/motion-tasks-2026-11-04.md: fault stops recover by themselves, a person's stop only clears on 'carry on' or reset, resets end
at Rest. Real gate, real watchdog, real supervisor, real threads; a slow fake arm so a stop can land MID-MOVE."""
import threading
import time

import pytest

from lookandpick.adapters.base import PixelHand  # noqa: F401
from lookandpick.adapters.fake import FakeTracker
from lookandpick.adapters.hands import FakeHandDetector
from lookandpick.hand_gate import HandGate
from lookandpick.models.base import ChatResult, FakeLLM, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime
from lookandpick.safety import Safety
from lookandpick.world import TrackedObject
from test_hand_gate import CAL, HAND_HEIGHT, FakeCamera, hand_at, wait_for

REST = (0.0, -0.12, 0.2)


class SlowArm:
    """Moves for ~0.5 s at full speed, respects the watchdog's speed and a halt (returns False mid-move), and can resume, cancel and go to Rest."""
    simulated = True

    def __init__(self):
        self.position, self.halted, self.speed, self.scales = (0.0, 0.10, 0.05), False, 0.0, []
        self.gripping, self.log, self.rest_position = False, [], REST
        self._cancel = threading.Event()
        self.duration = 0.5                      # seconds for a move at full speed

    def _go(self, target, cap):
        start, progress = self.position, 0.0
        self._cancel.clear()
        while progress < 1.0:
            if self.halted or self._cancel.is_set():
                return False
            progress += 0.02 * min(cap, self.speed) / self.duration
            self.position = tuple(a + (b - a) * min(progress, 1.0) for a, b in zip(start, target))
            time.sleep(0.02)
        self.position = tuple(target)
        return True

    def move_to(self, target, speed_scale):
        self.log.append(("move", tuple(target)))
        return self._go(target, speed_scale)

    def rest(self):
        self.log.append(("rest", None))
        return self._go(self.rest_position, 1.0)

    def grip(self, closed):
        if self.halted or self.speed <= 0:
            return False
        self.gripping = closed
        self.log.append(("grip", closed))
        return True

    def halt(self):
        self.halted, self.speed = True, 0.0

    def resume(self):
        self.halted = False

    def cancel(self):
        self._cancel.set()

    def set_speed_scale(self, scale):
        self.scales.append(scale)
        self.speed = 0.0 if self.halted else scale

    def action(self, name, **kw):
        if name == "reset":
            self.position, self.halted, self.gripping = (0.0, 0.10, 0.05), False, False
        return name


@pytest.fixture
def world():
    made = []

    def make(clear_s=0.4, script=None, red=(0.10, 0.10, 0.018), arm=None):
        detector, camera, arm = FakeHandDetector([]), FakeCamera(), arm or SlowArm()
        gate = HandGate(camera, lambda: detector, CAL, arm, hand_height=HAND_HEIGHT, warmup_s=1.0, safety=Safety.for_arm(arm))
        gate.detector, gate.camera_fake = detector, camera
        gate.start()
        assert wait_for(lambda: gate.status()["state"] == "clear")
        objects = FakeTracker([TrackedObject("obj1", "red cube", red)])
        model = FakeLLM(script or [ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'),)), ChatResult("Done.")])
        runtime = Runtime(model, gate.arm, tracker=gate.tracker(objects), log=CallLog(), say=lambda _: None, safety=gate.safety, gate=gate)
        runtime.recovery.clear_s, runtime.recovery.poll_s, runtime.recovery.retry_s = clear_s, 0.05, 0.1
        runtime.tools.poll_s = 0.02
        made.append((gate, runtime))
        return gate, runtime, arm, camera
    yield make
    for gate, runtime in made:
        runtime.recovery.close()
        runtime.tools.cancel()
        gate.close()


def run(runtime, text):
    out = []
    thread = threading.Thread(target=lambda: out.append(runtime.command(text)), daemon=True)
    thread.start()
    return thread, out


def mid_move(arm):
    return wait_for(lambda: arm.log and arm.log[-1][0] == "move" and arm.position != (0.0, 0.10, 0.05) and not arm.halted, 3)


def test_a_camera_stall_latches_then_recovers_after_two_seconds_clear_and_the_pick_completes_without_going_to_rest(world):
    gate, runtime, arm, camera = world(clear_s=0.4)
    thread, out = run(runtime, "pick up the red cube")
    assert mid_move(arm)
    camera.frozen = True                                                   # the camera stalls mid-move
    assert wait_for(lambda: runtime.safety.stopped and runtime.safety.stop_kind == "fault")
    assert runtime.motion_status()["stop"]["kind"] == "fault"
    time.sleep(0.3)
    assert thread.is_alive()                                               # the pick HOLDS, it does not fail
    camera.frozen = False                                                  # frames return
    thread.join(10)
    assert out and out[0].steps[0]["result"] == "picked red cube"
    assert not runtime.safety.stopped and not runtime.safety.holding
    assert ("rest", None) not in arm.log                                   # no Rest on a recovery
    assert runtime.recovery.resets_left() == 2


def test_a_stall_with_a_hand_in_the_path_does_not_recover_until_the_hand_leaves(world):
    gate, runtime, arm, camera = world(clear_s=0.3)
    thread, out = run(runtime, "pick up the red cube")
    assert mid_move(arm)
    camera.frozen = True
    assert wait_for(lambda: runtime.safety.stopped)
    gate.detector.detections = [hand_at(0.10, 0.10)]                       # a hand on the target
    camera.frozen = False
    time.sleep(1.2)
    assert thread.is_alive() and (runtime.safety.holding or runtime.safety.stopped)      # still held: the way is not clear
    gate.detector.detections = []
    thread.join(10)
    assert out and out[0].steps[0]["result"] == "picked red cube" and not runtime.safety.holding


def test_four_stalls_in_a_minute_stay_latched_and_say_so(world):
    gate, runtime, arm, camera = world(clear_s=0.05)
    said = []
    runtime._say = said.append
    for n in range(3):                                                     # three automatic recoveries are allowed
        camera.frozen = True
        assert wait_for(lambda: runtime.safety.stopped, 5)
        camera.frozen = False
        assert wait_for(lambda: not runtime.safety.stopped and not runtime.safety.holding, 8), f"recovery {n + 1} did not happen"
    camera.frozen = True                                                   # the fourth
    assert wait_for(lambda: runtime.safety.stopped, 5)
    camera.frozen = False
    time.sleep(1.0)
    assert runtime.safety.stopped and runtime.recovery.gave_up()
    assert any("keeps dropping out" in s for s in said)
    assert runtime.motion_status()["stop"]["recovering_in_s"] is None
    assert runtime.command("carry on").reply == "carrying on"              # a person can still clear it
    assert not runtime.safety.stopped


def test_a_stop_never_clears_by_itself_and_carry_on_resumes_the_pick(world):
    gate, runtime, arm, camera = world(clear_s=0.2)
    thread, out = run(runtime, "pick up the red cube")
    assert mid_move(arm)
    runtime.stop()
    assert runtime.safety.stopped and runtime.safety.stop_kind == "person"
    time.sleep(1.5)                                                        # far longer than any recovery
    assert runtime.safety.stopped and thread.is_alive()                    # still latched, the pick still waiting
    assert runtime.motion_status()["stop"] == {"kind": "person", "reason": "STOP requested", "recovering_in_s": None, "resets_left": 3}
    assert runtime.command("Carry on please").reply == "carrying on"
    thread.join(10)
    assert out and out[0].steps[0]["result"] == "picked red cube" and not runtime.safety.stopped
    assert ("rest", None) not in arm.log


def test_a_person_stop_is_never_downgraded_by_a_fault(world):
    gate, runtime, arm, camera = world()
    runtime.stop()
    camera.frozen = True
    time.sleep(0.6)                                                        # the watchdog fails too, as the stalled camera times out
    assert runtime.safety.stop_kind == "person"


def test_the_deck_reset_ends_at_rest_and_clears_the_stop(world):
    gate, runtime, arm, camera = world()
    runtime.stop()
    runtime.reset()
    assert not runtime.safety.stopped and wait_for(lambda: arm.log and arm.log[-1] == ("rest", None))
    assert arm.position == REST


def test_cancel_while_holding_the_cube_puts_it_down_then_goes_to_rest(world):
    script = [ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'), ToolCall("2", "place", '{"id": "obj1", "loc": [0.2, -0.1, 0.0]}'))),
              ChatResult("Done.")]
    gate, runtime, arm, camera = world(script=script)
    thread, out = run(runtime, "pick up the red cube and put it there")
    assert wait_for(lambda: any(e == ("move", (0.2, -0.1, 0.10)) for e in arm.log), 8)       # carrying it to the drop
    assert arm.gripping
    runtime.command("cancel")
    thread.join(15)
    assert not thread.is_alive() and out[0].reply == "cancelled"
    moves = [e for e in arm.log if e[0] == "move"]
    assert moves[-1][1][2] == pytest.approx(0.018 + 0.10) and moves[-1][1][:2] == (0.10, 0.10)       # back above where it was picked up
    assert ("grip", False) in arm.log and arm.log[-1] == ("rest", None) and arm.position == REST      # released there, then Rest
    assert not runtime.safety.stopped


def test_a_hand_in_the_way_to_rest_pauses_then_carries_on(world):
    gate, runtime, arm, camera = world()
    gate.detector.detections = [hand_at(0.0, 0.06)]                        # right by the arm, on the way to Rest
    assert wait_for(lambda: gate.status()["state"] == "stop")
    done = []
    thread = threading.Thread(target=lambda: done.append(runtime.tools.go_rest()), daemon=True)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive() and arm.position != REST
    gate.detector.detections = []
    thread.join(10)
    assert done == ["at rest"] and arm.position == REST


def test_go_rest_is_refused_while_a_person_stop_is_latched(world):
    gate, runtime, arm, camera = world()
    runtime.stop()
    assert runtime.tools.go_rest() == "blocked: stopped"
    assert ("rest", None) not in arm.log
