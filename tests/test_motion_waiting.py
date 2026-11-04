"""Task 1 of docs/motion-tasks-2026-11-04.md: a hand in the way is waited out, not given up on. Real watchdog and real threads."""
import threading
import time

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.hand_gate import TargetTrackingArm
from lookandpick.hand_safety import HandSafetyMonitor, MonitoredTracker
from lookandpick.models.base import ChatResult, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.models.orchestrator import CANCEL_WORDS, Orchestrator
from lookandpick.safety import SIM_STOP_DISTANCE_M, STOP_DISTANCE_M, Safety, speed_scale
from lookandpick.tools import Tools
from lookandpick.world import Hand, WorldState

IN_THE_WAY = [Hand("h", (0.25, 0.10, 0.02))]          # on top of the red cube


class Rig:
    """A stand-in camera loop publishing the current hands ~30 times a second to the REAL watchdog, with a Tools wired as Deck wires it."""

    def __init__(self, hands=(), remind_s=20.0):
        self.hands = list(hands)
        self.said: list[str] = []
        self.safety = Safety.for_arm(SimArm())
        self.arm = TargetTrackingArm(SimArm())
        self.world = WorldState()
        self.monitor = HandSafetyMonitor(self.world, self.safety, self.arm, self.arm.path)
        self.tools = Tools(self.world, MonitoredTracker(FakeTracker(), self.monitor), self.arm, self.safety,
                           say=self.said.append, announce=self.said.append)
        self.tools.remind_s, self.tools.poll_s = remind_s, 0.02
        self._stop = threading.Event()
        self.monitor.start()
        self._thread = threading.Thread(target=self._publish, daemon=True)
        self._thread.start()

    def _publish(self):
        while not self._stop.is_set():
            self.monitor.publish(list(self.hands), time.monotonic())
            self._stop.wait(1 / 30)

    def run(self, fn):
        out = []
        thread = threading.Thread(target=lambda: out.append(fn()), daemon=True)
        thread.start()
        return thread, out

    def close(self):
        self._stop.set()
        self._thread.join(1)
        self.monitor._shutdown.set()


@pytest.fixture
def rig():
    made = []

    def make(*args, **kwargs):
        made.append(Rig(*args, **kwargs))
        return made[-1]
    yield make
    for r in made:
        r.close()


def test_a_hand_at_the_start_then_removed_lets_the_pick_complete(rig):
    r = rig(IN_THE_WAY)
    thread, out = r.run(lambda: r.tools.pick("obj1"))
    time.sleep(0.4)
    assert thread.is_alive() and not r.arm._arm.gripping                  # waiting, not refused
    r.hands.clear()
    thread.join(3)
    assert out == ["picked red cube"] and r.arm._arm.gripping
    assert r.said[0] == "Pausing: a hand is in the way" and r.said.count("Pausing: a hand is in the way") == 1
    assert r.said[1] == "Clear, carrying on"
    assert not r.safety.stopped


def test_a_hand_that_stays_keeps_it_waiting_with_reminders_and_cancel_ends_it_without_a_latch(rig):
    r = rig(IN_THE_WAY, remind_s=0.15)
    thread, out = r.run(lambda: r.tools.pick("obj1"))
    time.sleep(1.0)                                                       # stands in for the two minutes: the loop has no time limit
    assert thread.is_alive()
    assert r.said.count("Pausing: a hand is in the way") == 1
    assert r.said.count("Still waiting for the way to clear") >= 3
    r.tools.cancel()
    thread.join(3)
    assert out == ["blocked: cancelled"]
    assert not r.safety.stopped and not r.arm._arm.halted and not r.arm._arm.gripping


def test_stop_during_the_wait_stops_at_once(rig):
    r = rig(IN_THE_WAY)
    thread, out = r.run(lambda: r.tools.pick("obj1"))
    time.sleep(0.3)
    started = time.monotonic()
    r.tools.stop()
    thread.join(2)
    assert not thread.is_alive() and time.monotonic() - started < 0.5
    assert out[0].startswith("blocked") and r.safety.stopped and r.arm._arm.halted
    assert "Clear, carrying on" not in r.said


def test_cancel_clears_for_the_next_instruction_but_a_stop_stays_latched(rig):
    r = rig()
    r.tools.cancel()
    assert r.tools.pick("obj1") == "blocked: cancelled"
    r.tools.begin_instruction()
    assert r.tools.pick("obj1") == "picked red cube"
    r.tools.stop()
    r.tools.begin_instruction()
    assert r.tools.place("obj1", (0.2, -0.1, 0)).startswith("blocked") and r.safety.stopped


def test_a_no_go_cube_is_refused_at_once_even_with_a_hand_in_the_way(rig):
    r = rig(IN_THE_WAY)

    class Guard:
        def violation(self, start, end):
            return "the path crosses a no-go zone (cube 1,2,3)"
    r.tools.zones = Guard()
    started = time.monotonic()
    assert r.tools.pick("obj1").startswith("blocked: the path crosses a no-go zone")
    assert time.monotonic() - started < 0.5


def test_cancel_words_are_caught_locally_and_end_the_running_instruction(rig):
    r = rig(IN_THE_WAY)
    log = CallLog()
    asked = []

    class Model:
        def chat(self, messages, tools=None, max_tokens=1500):
            asked.append(messages)
            return ChatResult("", (ToolCall("c1", "pick", '{"id":"obj1"}'),)) if len(asked) == 1 else ChatResult("never reached")
    orchestrator = Orchestrator(Model(), r.tools, log=log)
    thread, out = r.run(lambda: orchestrator.run("pick up the red cube"))
    time.sleep(0.3)
    assert thread.is_alive()
    said = orchestrator.run("Never mind")                                  # concurrent, like Deck: never queued behind the running command
    assert said.reply == "cancelled" and len(asked) == 1                    # no model call for the cancel
    thread.join(3)
    assert out[0].steps[0]["result"] == "blocked: cancelled" and out[0].reply == "cancelled" and len(asked) == 1
    assert not r.safety.stopped
    assert any("local cancel word" in e["input"] for e in log.entries())


def test_cancel_words():
    for text in ("cancel", "Cancel that", "never mind", "Nevermind.", "forget it"):
        assert CANCEL_WORDS.match(text)
    for text in ("stop", "cancel_order is a word", "pick up the cube", "carry on"):
        assert not CANCEL_WORDS.match(text) or text.startswith("cancel_")


def test_simulated_arm_stops_at_ten_centimetres_the_real_one_keeps_fifteen():
    assert Safety.for_arm(SimArm()).stop_distance_m == SIM_STOP_DISTANCE_M == 0.10
    assert Safety.for_arm(object()).stop_distance_m == STOP_DISTANCE_M == 0.15 and Safety().stop_distance_m == 0.15
    sim, real = Safety.for_arm(SimArm()), Safety()
    path = ((0.0, 0.0, 0.1), (0.4, 0.0, 0.1))
    near = [Hand("h", (0.2, 0.12, 0.1))]                                   # 12 cm off the path
    assert sim.allowed_speed(near, path) > 0 and real.allowed_speed(near, path) == 0
    assert speed_scale(0.5) == 1.0 and sim.allowed_speed([Hand("h", (0.2, 0.1, 0.1))], path) == 0   # 10 cm exactly still stops


def test_only_a_hand_in_the_way_is_worth_waiting_for():
    safety = Safety()
    path = ((0.0, 0.0, 0.1), (0.4, 0.0, 0.1))
    assert safety.hand_in_the_way([Hand("h", (0.2, 0.05, 0.1))], path)
    assert not safety.hand_in_the_way([Hand("h", (0.2, 1.0, 0.1))], path)             # clear
    assert not safety.hand_in_the_way([Hand("h", (float("nan"), 0, 0))], path)        # bad geometry is a refusal
    safety.stop()
    assert not safety.hand_in_the_way([Hand("h", (0.2, 0.05, 0.1))], path)            # a latch is not waited out


def test_a_missing_hand_gate_is_a_refusal_not_a_wait():
    from lookandpick.hand_gate import NO_GATE_HAND
    safety = Safety()
    assert not safety.hand_in_the_way([NO_GATE_HAND], ((0.0, 0.1, 0.05), (0.2, 0.1, 0.05)))
    tools = Tools(WorldState(), FakeTracker(hands=[NO_GATE_HAND]), SimArm(), safety, say=lambda _: None)
    started = time.monotonic()
    assert tools.pick("obj1").startswith("blocked") and time.monotonic() - started < 0.5
