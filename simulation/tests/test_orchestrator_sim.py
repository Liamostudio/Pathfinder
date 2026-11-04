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


def test_hand_next_to_the_target_holds_the_pick_until_it_leaves(bridge):
    import threading
    import time
    client, _ = bridge
    client.action("hand", position=[0.23, 0.14, 0.05])
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("Done.")])
    said = []
    runtime = Runtime(llm, client, log=CallLog(), say=said.append)
    done = []
    thread = threading.Thread(target=lambda: done.append(runtime.command("pick up the red cube")), daemon=True)
    thread.start()
    time.sleep(0.8)
    assert thread.is_alive() and client.state()["held"] is None                 # waiting, not refused, and nothing moved
    client.action("hand", position=None)                                        # the hand leaves
    thread.join(60)
    assert done and done[0].steps[0]["result"] == "picked red cube" and client.state()["held"] == "obj1"
    assert said[:1] == ["Pausing: a hand is in the way"] and "Clear, carrying on" in said


def test_a_hand_that_stays_can_be_cancelled_without_latching_the_simulation(bridge):
    import threading
    import time
    client, _ = bridge
    client.action("hand", position=[0.23, 0.14, 0.05])
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("never reached")])
    runtime = Runtime(llm, client, log=CallLog(), say=lambda t: None)
    done = []
    thread = threading.Thread(target=lambda: done.append(runtime.command("pick up the red cube")), daemon=True)
    thread.start()
    time.sleep(0.5)
    assert runtime.command("cancel").reply == "cancelled"
    thread.join(10)
    assert done and done[0].steps[0]["result"] == "blocked: cancelled"
    assert client.state()["stopped"] is False and client.state()["held"] is None


def test_stop_word_halts_the_simulation_without_the_model(bridge):
    client, _ = bridge
    llm = FakeLLM([])
    rt = Runtime(llm, client, log=CallLog(), say=lambda t: None)
    assert rt.command("stop").reply == "stopped"
    assert client.state()["stopped"] is True and llm.requests == []
    rt.reset()
    assert client.state()["stopped"] is False
