"""Task 3 of docs/motion-tasks-2026-11-04.md: keep going when the first attempt does not work, and give up only for reasons that will not change."""
import math

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.models.base import ChatResult, FakeLLM, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.models.orchestrator import SYSTEM_PROMPT, Orchestrator
from lookandpick.runtime import Runtime
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import TrackedObject, WorldState

RED = (0.10, 0.10, 0.018)


class Table(FakeTracker):
    """Objects that can be moved or hidden while the arm works."""

    def __init__(self, red=RED, blue=None):
        super().__init__([TrackedObject("obj1", "red cube", red)] + ([TrackedObject("obj2", "blue cube", blue)] if blue else []))
        self.hidden: set[str] = set()

    def objects(self):
        return [o for o in self._objects if o.id not in self.hidden]


class Arm(SimArm):
    """Adds the hooks these tests need: run something after the Nth move, miss the first N closes, report an out-of-reach reason."""

    def __init__(self):
        super().__init__()
        self.after_move = {}
        self.moves = 0
        self.misses = 0
        self.reason = "ready"
        self.unreachable_after_hold = False

    def move_to(self, target, speed_scale):
        if self.unreachable_after_hold and self.gripping:
            self.reason = "Target outside simulation workspace (metres, arm-base frame)"
            return False
        done = super().move_to(target, speed_scale)
        self.moves += 1
        if self.moves in self.after_move:
            self.after_move[self.moves]()
        return done

    def grip(self, closed):
        if closed and self.misses > 0:
            self.misses -= 1
            self.log.append(("missed_grip", None))
            self.reason = "gripper closed; no object in grasp range"
            return False
        return super().grip(closed)

    def state(self):
        return {"reason": self.reason}


def rig(table=None, arm=None):
    said = []
    table, arm = table or Table(), arm or Arm()
    safety = Safety.for_arm(arm)
    tools = Tools(WorldState(), table, arm, safety, say=said.append, announce=said.append)
    tools.look_s, tools.poll_s = 0.8, 0.02
    return tools, table, arm, said


def nudge(table, dx, dy):
    o = table._objects[0]
    table._objects[0] = TrackedObject(o.id, o.label, (o.position[0] + dx, o.position[1] + dy, o.position[2]))


def test_a_target_nudged_three_centimetres_during_the_approach_is_still_picked():
    tools, table, arm, said = rig()
    arm.after_move[1] = lambda: nudge(table, 0.03, 0.0)                    # someone knocks it as the arm arrives overhead
    assert tools.pick("obj1") == "picked red cube"
    descent = [e[1][0] for e in arm.log if e[0] == "move" and e[1][0][2] < 0.05]
    assert descent and descent[0] == pytest.approx((0.13, 0.10, 0.018))     # it went to where the cube NOW is
    assert not tools.gave_up


def test_a_first_grip_that_misses_is_retried_and_succeeds():
    tools, table, arm, said = rig()
    arm.misses = 1
    assert tools.pick("obj1") == "picked red cube"
    assert ("missed_grip", None) in arm.log and "Missed it, trying again" in said
    assert ("grip", False) in arm.log and not tools.gave_up                 # opened, backed off, tried again


def test_three_missed_grips_give_up_with_one_sentence_and_the_runtime_folds_back_to_rest():
    tools, table, arm, said = rig()
    arm.misses = 5
    rt = Runtime(FakeLLM([ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'),)), ChatResult("I could not.")]), arm, tracker=table, log=CallLog(),
                 say=said.append)
    rt.tools.look_s = 0.5
    result = rt.command("pick up the red cube")
    assert arm.log.count(("missed_grip", None)) == 3
    assert result.steps[0]["result"] == "blocked: missed the red cube 3 times"
    assert "I keep missing the red cube; check it is within reach and I'll try again." in said
    assert arm.log[-1] == ("rest", None) and arm.position == arm.rest_position


def test_an_object_that_vanishes_gives_cant_find_it_after_the_look_again_window_then_rest():
    tools, table, arm, said = rig()
    arm.after_move[1] = lambda: table.hidden.add("obj1")
    rt = Runtime(FakeLLM([ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'),)), ChatResult("Gone.")]), arm, tracker=table, log=CallLog(), say=said.append)
    rt.tools.look_s, rt.tools.poll_s = 0.8, 0.02
    result = rt.command("pick up the red cube")
    assert "Lost sight of the red cube, looking again" in said
    assert result.steps[0]["result"].startswith("blocked: cannot find the red cube after looking")
    assert "I can't find the red cube; put it back in view and I'll try again." in said
    assert arm.position == arm.rest_position


def test_an_object_that_comes_back_into_view_is_found_and_picked():
    tools, table, arm, said = rig()
    arm.after_move[1] = lambda: table.hidden.add("obj1")
    looks = []

    def relook():                                                          # the SAM look-again finds it on the second try
        looks.append(1)
        if len(looks) == 2:
            table.hidden.clear()
    tools.relook = relook
    tools.look_s = 5.0
    assert tools.pick("obj1") == "picked red cube" and len(looks) == 2 and "Lost sight of the red cube, looking again" in said


def test_an_unreachable_drop_explains_puts_the_cube_back_and_goes_to_rest():
    tools, table, arm, said = rig()
    script = [ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'), ToolCall("2", "place", '{"id": "obj1", "loc": [0.9, 0.9, 0.0]}'))), ChatResult("Done.")]
    rt = Runtime(FakeLLM(script), arm, tracker=table, log=CallLog(), say=said.append)
    original = arm.move_to

    def move_to(target, scale):                                            # the drop at (0.9, 0.9) cannot be reached; everything else can
        if abs(target[0]) > 0.5:
            arm.reason = "Target outside simulation workspace (metres, arm-base frame)"
            return False
        arm.reason = "ready"
        return original(target, scale)
    arm.move_to = move_to
    result = rt.command("put the red cube on the shelf")
    assert result.steps[0]["result"] == "picked red cube" and result.steps[1]["result"] == "blocked: the drop spot is out of reach"
    assert "I can't reach that spot from here; choose a spot nearer the arm and I'll try again." in said
    puts = [e for e in arm.log if e == ("grip", False)]
    assert puts and arm.position == arm.rest_position and arm.log[-1] == ("rest", None)       # released where it was picked up, then Rest
    moves = [e[1][0] for e in arm.log if e[0] == "move"]
    assert any(m[:2] == (0.10, 0.10) and m[2] < 0.05 for m in moves[2:])              # it went back down at the pick-up spot to release the cube


def test_a_taken_drop_spot_is_refused_with_the_reason_so_the_model_can_choose_a_free_one():
    table = Table(blue=(0.20, -0.15, 0.018))
    tools, table, arm, said = rig(table)
    assert tools.pick("obj1") == "picked red cube"
    out = tools.place("obj1", (0.20, -0.14, 0.018))
    assert out == "blocked: that spot is taken by the blue cube; choose a free spot beside it"
    assert not tools.gave_up                                               # a model can work around it, so no Rest
    assert tools.place("obj1", (0.20, -0.22, 0.018)) == "placed red cube"


def test_the_orchestrator_chooses_a_free_spot_after_a_blocked_drop():
    table = Table(blue=(0.20, -0.15, 0.018))
    tools, table, arm, said = rig(table)
    script = [ChatResult("", (ToolCall("1", "pick", '{"id": "obj1"}'), ToolCall("2", "place", '{"id": "obj1", "loc": [0.2, -0.14, 0.018]}'))),
              ChatResult("", (ToolCall("3", "place", '{"id": "obj1", "loc": [0.2, -0.22, 0.018]}'),)), ChatResult("Placed it beside the blue cube.")]
    out = Orchestrator(FakeLLM(script), tools, log=CallLog()).run("put the red cube on the blue cube")
    assert [s["result"] for s in out.steps][-1] == "placed red cube" and out.reply == "Placed it beside the blue cube."


def test_the_prompt_asks_for_persistence_within_the_instruction_and_no_invented_goals():
    for needle in ("Keep working", "free spot beside it", "Never invent a new goal", "never move an object the user did not ask about",
                   "ONE short question", "one short sentence"):
        assert needle in SYSTEM_PROMPT


def test_with_a_camera_tracker_a_grip_that_did_not_lift_the_object_is_retried():
    tools, table, arm, said = rig()
    tools.verify_lift = True
    state = {"closed": 0}
    original_grip = arm.grip

    def grip(closed):
        done = original_grip(closed)
        if closed:
            state["closed"] += 1
        return done
    arm.grip = grip
    original_move = arm.move_to

    def move_to(target, scale):                                            # on the first close the cube stays put; on the second it rises with the gripper
        done = original_move(target, scale)
        if state["closed"] >= 2 and arm.gripping:
            o = table._objects[0]
            table._objects[0] = TrackedObject(o.id, o.label, tuple(arm.position))
        return done
    arm.move_to = move_to
    assert tools.pick("obj1") == "picked red cube" and state["closed"] == 2
