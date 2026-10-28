"""Model client and orchestrator, against a local fake API and scripted models (no network, no cost)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.models.base import ChatResult, FakeLLM, ModelError, SpendCapExceeded, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.models.meta import MetaClient, cost_usd, load_env
from lookandpick.models.orchestrator import TOOL_SCHEMAS, Orchestrator
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import Hand, WorldState


class FakeAPI:
    """Serves queued (status, body) replies and records requests."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, payload = outer.replies.pop(0)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def reply(content="ok", tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return 200, {"choices": [{"message": msg, "finish_reason": "stop"}],
                 "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100,
                                    "completion_tokens_details": {"reasoning_tokens": 60}}}


@pytest.fixture
def api():
    servers = []

    def make(replies):
        s = FakeAPI(replies)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def client(api_, **kw):
    kw.setdefault("backoff_s", 0.0)
    return MetaClient("test-key", base_url=api_.url, **kw)


def test_successful_call_is_logged_with_cost_and_tool_calls(api):
    a = api([reply("", [{"id": "c1", "type": "function", "function": {"name": "pick", "arguments": '{"id":"obj1"}'}}])])
    c = client(a)
    result = c.chat([{"role": "user", "content": "pick the cube"}], TOOL_SCHEMAS)
    assert result.tool_calls == (ToolCall("c1", "pick", '{"id":"obj1"}'),)
    assert a.requests[0]["auth"] == "Bearer test-key" and a.requests[0]["path"].endswith("/chat/completions")
    assert a.requests[0]["body"]["max_tokens"] >= 400  # reasoning budget
    entry = c.log.entries()[0]
    assert entry["source"] == "Muse Spark" and entry["status"] == "ok" and "pick" in entry["output"]
    assert entry["cost_usd"] == pytest.approx((1000 * 1.25 + 100 * 4.25) / 1e6)
    assert entry["tokens"]["reasoning"] == 60


def test_cost_uses_cached_input_price():
    usage = {"prompt_tokens": 1000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 800}}
    assert cost_usd("muse-spark-1.3", usage) == pytest.approx((200 * 1.25 + 800 * 0.15) / 1e6)
    assert cost_usd("muse-spark-1.3-contributor", {"prompt_tokens": 1_000_000}) == pytest.approx(0.10)


def test_retries_on_rate_limit_then_succeeds(api):
    a = api([(429, {}), reply("fine")])
    assert client(a).chat([{"role": "user", "content": "hi"}]).content == "fine"
    assert len(a.requests) == 2


def test_gives_up_after_retries_and_logs_error(api):
    a = api([(500, {})] * 3)
    c = client(a, retries=2)
    with pytest.raises(ModelError, match="unavailable"):
        c.chat([{"role": "user", "content": "hi"}])
    assert c.log.entries()[-1]["status"] == "error" and len(a.requests) == 3


def test_bad_request_is_not_retried(api):
    a = api([(401, {})])
    with pytest.raises(ModelError, match="401"):
        client(a).chat([{"role": "user", "content": "hi"}])
    assert len(a.requests) == 1


@pytest.mark.parametrize("payload", [b"not json", {"unexpected": True}])
def test_malformed_replies_are_errors(api, payload):
    a = api([(200, payload)])
    with pytest.raises(ModelError):
        client(a).chat([{"role": "user", "content": "hi"}])


def test_spend_cap_stops_calls_without_requesting(api):
    a = api([reply(usage={"prompt_tokens": 1_000_000, "completion_tokens": 0}), reply()])
    c = client(a, spend_cap_usd=1.0)
    c.chat([{"role": "user", "content": "big"}])  # costs $1.25, over the cap
    with pytest.raises(SpendCapExceeded):
        c.chat([{"role": "user", "content": "again"}])
    assert len(a.requests) == 1 and c.log.entries()[-1]["status"] == "blocked"


def test_call_cap(api):
    a = api([reply(), reply()])
    c = client(a, max_calls=1)
    c.chat([{"role": "user", "content": "one"}])
    with pytest.raises(SpendCapExceeded):
        c.chat([{"role": "user", "content": "two"}])


def test_missing_key_rejected():
    with pytest.raises(ModelError, match="MODEL_API_KEY"):
        MetaClient("")


def test_load_env_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# c\nMODEL_API_KEY='abc'\nOTHER=1\n")
    monkeypatch.setenv("OTHER", "keep")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    load_env(f)
    import os
    assert os.environ["MODEL_API_KEY"] == "abc" and os.environ["OTHER"] == "keep"
    monkeypatch.delenv("MODEL_API_KEY")


# --- orchestrator, through the real Tools and Safety with fake adapters ---

def stack():
    tracker, arm, safety = FakeTracker(), SimArm(), Safety()
    said = []
    tools = Tools(WorldState(), tracker, arm, safety, say=said.append)
    return tracker, arm, safety, tools, said


def call(name, args, id="c"):
    return ToolCall(id, name, json.dumps(args))


def test_scripted_pick_runs_through_tools():
    _, arm, _, tools, _ = stack()
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("Picked it.")])
    out = Orchestrator(llm, tools).run("pick up the red cube")
    assert out.steps[0]["result"] == "picked red cube" and arm.gripping and out.reply == "Picked it."
    # the scene went to the model, and the tool result came back to it
    assert "obj1" in llm.requests[0]["messages"][1]["content"]
    assert llm.requests[1]["messages"][-1]["role"] == "tool"


def test_hand_blocks_pick_and_model_sees_the_refusal():
    tracker, arm, _, tools, _ = stack()
    tracker.set_hands([Hand("h", (0.25, 0.10, 0.02))])
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("A hand is in the way.")])
    out = Orchestrator(llm, tools).run("pick up the red cube")
    assert out.steps[0]["result"].startswith("blocked") and not arm.gripping
    assert llm.requests[1]["messages"][-1]["content"].startswith("blocked")


def test_stop_word_bypasses_the_model():
    _, arm, safety, tools, _ = stack()
    llm = FakeLLM([])
    out = Orchestrator(llm, tools).run("Stop!")
    assert safety.stopped and arm.halted and out.reply == "stopped" and llm.requests == []


def test_model_can_call_stop_too():
    _, arm, safety, tools, _ = stack()
    llm = FakeLLM([ChatResult("", (call("stop", {}),)), ChatResult("Stopped.")])
    Orchestrator(llm, tools).run("something odd")
    assert safety.stopped


@pytest.mark.parametrize("bad", [
    ToolCall("c", "delete_everything", "{}"),                 # unknown tool
    ToolCall("c", "pick", "not json"),                        # malformed arguments
    ToolCall("c", "pick", '{"id": 5}'),                       # wrong type
    ToolCall("c", "pick", "[1]"),                             # not an object
    ToolCall("c", "place", '{"id":"obj1","loc":[0,0]}'),      # wrong length
    ToolCall("c", "place", '{"id":"obj1","loc":[0,0,"x"]}'),  # wrong element type
    ToolCall("c", "place", '{"id":"obj1","loc":[0,0,1e999]}'),  # non-finite
])
def test_invalid_tool_calls_never_reach_the_arm(bad):
    _, arm, _, tools, _ = stack()
    llm = FakeLLM([ChatResult("", (bad,)), ChatResult("Sorry.")])
    out = Orchestrator(llm, tools).run("do it")
    assert out.steps[0]["result"].startswith("error") and not arm.gripping and arm.position == (0.0, 0.0, 0.2)


def test_unknown_object_is_reported_back():
    _, _, _, tools, _ = stack()
    llm = FakeLLM([ChatResult("", (call("pick", {"id": "obj99"}),)), ChatResult("No such object.")])
    assert Orchestrator(llm, tools).run("pick the unicorn").steps[0]["result"].startswith("error: no object")


def test_say_goes_to_the_speaker():
    _, _, _, tools, said = stack()
    llm = FakeLLM([ChatResult("", (call("say", {"text": "Which one?"}),)), ChatResult("")])
    Orchestrator(llm, tools).run("pick it")
    assert said == ["Which one?"]


def test_say_only_turn_ends_without_another_model_call():
    _, _, _, tools, said = stack()
    llm = FakeLLM([ChatResult("", (call("say", {"text": "No banana here."}),))])
    out = Orchestrator(llm, tools).run("pick the banana")
    assert said == ["No banana here."] and len(llm.requests) == 1 and not out.error


def test_loop_is_bounded():
    _, _, _, tools, _ = stack()
    llm = FakeLLM(lambda m: ChatResult("", (call("get_scene", {}),)))
    out = Orchestrator(llm, tools, max_steps=3).run("loop")
    assert "step limit" in out.error and len(llm.requests) == 3


def test_model_failure_is_reported_not_raised():
    _, arm, _, tools, _ = stack()
    out = Orchestrator(FakeLLM([]), tools).run("pick up the red cube")
    assert out.error and not arm.gripping


def test_tool_schemas_are_exactly_the_five_tools():
    assert {t["function"]["name"] for t in TOOL_SCHEMAS} == {"get_scene", "pick", "place", "stop", "say"}
