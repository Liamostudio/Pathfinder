"""Muse Spark (or Glimmer) as an orchestrator that can only call the five tools.

The model never sees the motors. It proposes tool calls; each is validated here and executed through
`Tools`, which applies the safety layer. "Stop" is handled locally without asking the model, and the
model's own `stop` call is also just `Tools.stop()`.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field

from ..tools import Tools
from .base import ChatModel, ModelError, ToolCall
from .calllog import CallLog

SYSTEM_PROMPT = (
    "You control a small table-mounted robot arm through tools. Use only the tools provided. "
    "The scene lists objects with ids, labels and positions in metres (x, y across the table, z up). "
    "Pick and place only objects that exist in the scene; if the request is ambiguous or impossible, "
    "use say() to ask or explain instead of guessing. Call stop() immediately if asked to stop. "
    "Keep say() messages short. Tool results tell you whether the safety layer allowed the action; "
    "if blocked, say so briefly and do not retry in a loop."
)

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "get_scene", "description": "Current objects, hands and arm position.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "pick", "description": "Pick up the object with this id.",
                                      "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                                                     "required": ["id"]}}},
    {"type": "function", "function": {"name": "place", "description": "Place the held object at a table position (metres).",
                                      "parameters": {"type": "object", "properties": {
                                          "id": {"type": "string"},
                                          "loc": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
                                          "required": ["id", "loc"]}}},
    {"type": "function", "function": {"name": "stop", "description": "Stop all motion immediately.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "say", "description": "Say a short message to the user.",
                                      "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                                                     "required": ["text"]}}},
]

STOP_WORDS = re.compile(r"^\W*(stop|halt|freeze|emergency stop|abort)\b", re.IGNORECASE)


@dataclass
class RunResult:
    command: str
    steps: list[dict] = field(default_factory=list)  # executed tool calls: name, args, result
    reply: str = ""
    error: str = ""


class Orchestrator:
    def __init__(self, model: ChatModel, tools: Tools, max_steps: int = 6, max_tool_calls: int = 8,
                 log: CallLog | None = None) -> None:
        self.model, self.tools, self.log = model, tools, log
        self.max_steps, self.max_tool_calls = max_steps, max_tool_calls

    def stop(self) -> str:
        """Local, instant, never involves the model."""
        return self.tools.stop()

    def run(self, command: str) -> RunResult:
        result = RunResult(command)
        if STOP_WORDS.match(command):
            started = time.monotonic()
            result.steps.append({"name": "stop", "args": {}, "result": self.stop()})
            if self.log is not None:
                self.log.record("Safety", f"{command!r} (local stop word, no model call)", "stopped",
                                (time.monotonic() - started) * 1000, status="blocked")
            result.reply = "stopped"
            return result
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Scene: {json.dumps(self.tools.get_scene())}\n\nRequest: {command}"}]
        executed = 0
        for _ in range(self.max_steps):
            if self.tools.safety.stopped:  # after any stop, do not keep asking the model to try again
                result.reply = result.reply or "stopped"
                return result
            try:
                reply = self.model.chat(messages, TOOL_SCHEMAS)
            except ModelError as e:
                result.error = str(e)
                return result
            if not reply.tool_calls:
                result.reply = reply.content
                return result
            messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in reply.tool_calls]})
            for call in reply.tool_calls:
                executed += 1
                outcome = "error: too many tool calls in one request" if executed > self.max_tool_calls \
                    else self._execute(call, result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome})
            if self.tools.safety.stopped:
                result.reply = result.reply or "stopped"
                return result
            if all(c.name == "say" for c in reply.tool_calls):
                result.reply = result.reply or reply.content  # nothing left to do: skip a round trip (~3 s, ~1 cent)
                return result
        result.error = result.error or "stopped after the step limit"
        return result

    def _execute(self, call: ToolCall, result: RunResult) -> str:
        started = time.monotonic()
        try:
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            outcome = self._dispatch(call.name, args)
        except (ValueError, TypeError) as e:
            outcome = f"error: {e}"
        result.steps.append({"name": call.name, "args": call.arguments, "result": outcome})
        if self.log is not None:
            self.log.record("Tools", f"{call.name}({call.arguments})", outcome[:300], (time.monotonic() - started) * 1000,
                            status="blocked" if outcome.startswith(("blocked", "error")) else "ok")
        return outcome

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "get_scene":
            return json.dumps(self.tools.get_scene())
        if name == "stop":
            return self.stop()
        if name == "say":
            text = args.get("text")
            if not isinstance(text, str):
                raise ValueError("say needs a text string")
            self.tools.say(text[:300])
            return "said"
        if name == "pick":
            ident = args.get("id")
            if not isinstance(ident, str):
                raise ValueError("pick needs an id string")
            return self.tools.pick(ident)
        if name == "place":
            ident, loc = args.get("id"), args.get("loc")
            if not isinstance(ident, str):
                raise ValueError("place needs an id string")
            if (not isinstance(loc, list) or len(loc) != 3
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in loc)):
                raise ValueError("place needs loc as three finite numbers in metres")
            return self.tools.place(ident, (float(loc[0]), float(loc[1]), float(loc[2])))
        raise ValueError(f"unknown tool {name!r}")
