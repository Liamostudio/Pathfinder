"""The orchestrator driving the MuJoCo simulation over the bridge, with a scripted model (no network)."""
import json

from lookandpick.models.base import ChatResult, FakeLLM, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime
from test_bridge import bridge  # noqa: F401  (fixture)


def call(name, args):
    return ToolCall("c", name, json.dumps(args))


def positions(client):
    return {o["id"]: o["position"] for o in client.state()["scene"]["objects"]}


def test_scripted_model_picks_and_places_in_the_simulation(bridge):
    client, _ = bridge
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}), call("place", {"id": "obj1", "loc": [0.18, -0.2, 0.018]}))),
                   ChatResult("Done.")])
    rt = Runtime(llm, client, log=CallLog(), say=lambda t: None)
    out = rt.command("pick up the red cube and put it over there")
    assert [s["result"] for s in out.steps] == ["picked red cube", "placed red cube"]
    x, y, _ = positions(client)["obj1"]
    assert abs(x - 0.18) < 0.03 and abs(y + 0.2) < 0.03
    assert client.state()["held"] is None


def test_hand_next_to_the_target_blocks_the_pick_and_the_model_is_told(bridge):
    client, _ = bridge
    client.action("hand", position=[0.23, 0.14, 0.05])
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("blocked")])
    out = Runtime(llm, client, log=CallLog(), say=lambda t: None).command("pick up the red cube")
    assert out.steps[0]["result"].startswith("blocked") and client.state()["held"] is None
    assert llm.requests[1]["messages"][-1]["content"].startswith("blocked")


def test_stop_word_halts_the_simulation_without_the_model(bridge):
    client, _ = bridge
    llm = FakeLLM([])
    rt = Runtime(llm, client, log=CallLog(), say=lambda t: None)
    assert rt.command("stop").reply == "stopped"
    assert client.state()["stopped"] is True and llm.requests == []
    rt.reset()
    assert client.state()["stopped"] is False
