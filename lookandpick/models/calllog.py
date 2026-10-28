"""A record of every model call: name, input, output, latency and cost.

Entries use the same keys as Deck's event timeline (`source`, `input`, `output`, `latency_ms`,
`status`, `time`) plus `cost_usd` and token counts, so they can be shown there unchanged.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone


class CallLog:
    def __init__(self, keep: int = 200) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._next_id = 0
        self._keep = keep
        self.total_cost_usd = 0.0
        self.calls = 0

    def record(self, source: str, input: str, output: str, latency_ms: float, *, status: str = "ok",
               cost_usd: float = 0.0, tokens: dict | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            entry = {
                "id": self._next_id, "time": datetime.now(timezone.utc).isoformat(),
                "source": source, "input": input, "output": output,
                "latency_ms": round(latency_ms, 1), "status": status,
                "cost_usd": round(cost_usd, 6), "tokens": tokens or {}, "scripted": False,
            }
            self._entries = (self._entries + [entry])[-self._keep:]
            self.total_cost_usd += cost_usd
            self.calls += 1
            return dict(entry)

    def entries(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._entries]
