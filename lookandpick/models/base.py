"""The chat interface the orchestrator needs, and a scripted stand-in for tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class ModelError(RuntimeError):
    """A model call failed (network, HTTP error, malformed reply)."""


class SpendCapExceeded(ModelError):
    """The configured spend or call cap was reached; no further calls are made."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text from the model; validated by the orchestrator, never trusted


@dataclass(frozen=True)
class ChatResult:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict = field(default_factory=dict)


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1500) -> ChatResult: ...


class FakeLLM:
    """Replays scripted results (or calls a function), so tests are deterministic and free."""

    name = "Fake LLM"

    def __init__(self, script: list[ChatResult] | Callable[[list[dict]], ChatResult]) -> None:
        self._script, self.requests = script, []

    def chat(self, messages, tools=None, max_tokens=1500) -> ChatResult:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if callable(self._script):
            return self._script(messages)
        if not self._script:
            raise ModelError("fake script exhausted")
        return self._script.pop(0)
