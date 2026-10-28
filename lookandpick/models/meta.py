"""Client for Meta's OpenAI-compatible Model API (Muse Spark), standard library only.

Never used on the safety path. Every call is logged with latency and cost, and a hard spend and
call-count cap stops further calls rather than overspending the hackathon credit.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import ChatResult, ModelError, SpendCapExceeded, ToolCall
from .calllog import CallLog

BASE_URL = "https://api.meta.ai/v1"
# USD per million tokens: (input, cached input, output). From dev.meta.ai/models/muse-spark, 28 Oct 2026.
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def load_env(path: Path | str = ".env") -> None:
    """Minimal .env reader; real environment variables win. Never prints values."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cost_usd(model: str, usage: dict) -> float:
    price_in, price_cached, price_out = PRICES.get(model, PRICES["muse-spark-1.3"])  # unknown model: assume standard
    prompt = int(usage.get("prompt_tokens", 0))
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    out = int(usage.get("completion_tokens", 0))
    return ((prompt - cached) * price_in + cached * price_cached + out * price_out) / 1e6


class MetaClient:
    name = "Muse Spark"

    def __init__(self, api_key: str, model: str = "muse-spark-1.3", *, base_url: str = BASE_URL,
                 log: CallLog | None = None, spend_cap_usd: float = 2.0, max_calls: int = 300,
                 timeout_s: float = 60.0, retries: int = 2, backoff_s: float = 1.0) -> None:
        if not api_key:
            raise ModelError("no API key: set MODEL_API_KEY in .env")
        self._key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.log = log or CallLog()
        self.spend_cap_usd, self.max_calls = spend_cap_usd, max_calls
        self.timeout_s, self.retries, self.backoff_s = timeout_s, retries, backoff_s

    @classmethod
    def from_env(cls, log: CallLog | None = None, env_file: Path | str = ".env") -> "MetaClient":
        load_env(env_file)
        return cls(os.environ.get("MODEL_API_KEY", ""), os.environ.get("MUSE_SPARK_MODEL", "muse-spark-1.3"),
                   base_url=os.environ.get("MODEL_API_BASE_URL", BASE_URL), log=log,
                   spend_cap_usd=float(os.environ.get("MODEL_SPEND_CAP_USD", "2.0")),
                   max_calls=int(os.environ.get("MODEL_MAX_CALLS", "300")))

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        last = "no attempt made"
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in RETRY_STATUS:
                    raise ModelError(last) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"network error: {type(e).__name__}"
            except json.JSONDecodeError:
                raise ModelError("malformed JSON from the model API") from None
            if attempt < self.retries:
                time.sleep(self.backoff_s * (2 ** attempt))
        raise ModelError(f"model API unavailable after {self.retries + 1} attempts ({last})")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1500) -> ChatResult:
        # Spark always reasons first; a small max_tokens is spent before any answer appears.
        if self.log.calls >= self.max_calls or self.log.total_cost_usd >= self.spend_cap_usd:
            self.log.record(self.name, _summary(messages), "spend/call cap reached, call not made", 0, status="blocked")
            raise SpendCapExceeded(f"cap reached: {self.log.calls} calls, ${self.log.total_cost_usd:.4f}")
        body = {"model": self.model, "messages": messages, "max_tokens": max(max_tokens, 400)}
        if tools:
            body["tools"] = tools
        started = time.monotonic()
        try:
            data = self._post(body)
            choice = data["choices"][0]["message"]
            calls = tuple(ToolCall(str(c.get("id", "")), c["function"]["name"], c["function"].get("arguments") or "{}")
                          for c in (choice.get("tool_calls") or []))
            result = ChatResult(choice.get("content") or "", calls, data.get("usage") or {})
        except ModelError as e:
            self.log.record(self.name, _summary(messages), str(e), (time.monotonic() - started) * 1000, status="error")
            raise
        except (KeyError, IndexError, TypeError):
            self.log.record(self.name, _summary(messages), "unexpected reply shape", (time.monotonic() - started) * 1000, status="error")
            raise ModelError("unexpected reply shape from the model API") from None
        usage = result.usage
        out = result.content + "".join(f" [tool {c.name}({c.arguments})]" for c in calls)
        self.log.record(self.name, _summary(messages), out.strip() or "(empty)", (time.monotonic() - started) * 1000,
                        cost_usd=cost_usd(self.model, usage),
                        tokens={"in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0),
                                "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)})
        return result


def _summary(messages: list[dict]) -> str:
    last = messages[-1] if messages else {}
    return str(last.get("content", ""))[:300]
