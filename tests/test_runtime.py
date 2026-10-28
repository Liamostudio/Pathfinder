"""Runtime wiring with fake adapters: command -> model -> tools -> arm, reset, timeline, Glimmer client."""
import json

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.models.base import ChatResult, FakeLLM, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.models.meta import MetaClient
from lookandpick.runtime import Runtime


def call(name, args):
    return ToolCall("c", name, json.dumps(args))


def test_command_runs_through_the_real_tools_and_records_a_timeline():
    arm, said = SimArm(), []
    rt = Runtime(FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("Done.")]), arm, FakeTracker(),
                 log=CallLog(), say=said.append)
    out = rt.command("pick up the red cube")
    assert out.steps[0]["result"] == "picked red cube" and arm.gripping
    assert [e["source"] for e in rt.timeline()] == ["Tools"]


def test_stop_then_reset_clears_the_latch():
    arm = SimArm()
    rt = Runtime(FakeLLM([]), arm, FakeTracker(), log=CallLog(), say=lambda t: None)
    assert rt.stop() == "stopped" and rt.safety.stopped and arm.halted
    out = rt.command("pick up the red cube")  # after a stop the model is not even asked
    assert out.reply == "stopped" and out.steps == [] and not arm.gripping
    rt.reset()
    assert not rt.safety.stopped


def test_glimmer_client_is_local_and_free():
    log = CallLog()
    g = MetaClient.glimmer_from_env(log, env_file="/nonexistent")
    assert g.name == "Muse Glimmer" and g.free and g.base_url.startswith("http://127.0.0.1")
    assert g.spend_cap_usd == float("inf")
