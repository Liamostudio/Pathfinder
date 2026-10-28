"""Puts the pieces together: a model (Muse Spark or Glimmer) -> Orchestrator -> Tools/Safety -> arm and tracker.

    python -m lookandpick.runtime "pick up the red cube and put it at 0.18 -0.2"
    python -m lookandpick.runtime --repl --model glimmer

The default arm and tracker are the MuJoCo simulation over its local bridge (start it with
`simulation/start.py`). Nothing here talks to a serial port or the real arm. Every model call and every
tool execution is recorded in `runtime.log`, in the same shape as Deck's timeline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models.base import ChatModel
from .models.calllog import CallLog
from .models.meta import MetaClient
from .models.orchestrator import Orchestrator, RunResult
from .safety import Safety
from .tools import Tools
from .world import WorldState


class Runtime:
    def __init__(self, model: ChatModel, arm=None, tracker=None, log: CallLog | None = None,
                 say=print, safety: Safety | None = None) -> None:
        if arm is None:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))
            from sim_env.client import SimulationClient  # noqa: PLC0415
            arm = SimulationClient()
        self.arm, self.tracker = arm, tracker or arm
        self.log = log or getattr(model, "log", None) or CallLog()
        self.safety = safety or Safety()
        self.said: list[str] = []
        self._say = say
        self.tools = Tools(WorldState(), self.tracker, self.arm, self.safety, say=self._speak)
        self.model = model
        self.orchestrator = Orchestrator(model, self.tools, log=self.log)

    def _speak(self, text: str) -> None:
        self.said.append(text)
        self._say(text)

    def command(self, text: str) -> RunResult:
        return self.orchestrator.run(text)

    def stop(self) -> str:
        return self.orchestrator.stop()

    def reset(self) -> None:
        """Clear the stop latch and put the simulation back. Never used on the safety path."""
        self.safety.reset()
        if hasattr(self.arm, "action"):
            self.arm.action("reset")

    def timeline(self) -> list[dict]:
        return self.log.entries()


def make_model(kind: str, log: CallLog) -> ChatModel:
    if kind == "spark":
        return MetaClient.from_env(log)
    if kind == "glimmer":
        return MetaClient.glimmer_from_env(log)
    raise ValueError(f"unknown model {kind!r}")


def print_result(runtime: Runtime, result: RunResult, since: int = 0) -> None:
    for step in result.steps:
        print(f"  tool {step['name']} {step['args']} -> {step['result']}")
    print(f"  reply: {result.reply or '(none)'}" + (f"   ERROR: {result.error}" if result.error else ""))
    entries = runtime.timeline()[since:]
    calls = [e for e in entries if e["source"] in ("Muse Spark", "Muse Glimmer")]
    if calls:
        print(f"  model: {len(calls)} calls, {sum(e['latency_ms'] for e in calls) / 1000:.1f} s, "
              f"${sum(e['cost_usd'] for e in calls):.4f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="*", help="what to ask the arm to do")
    p.add_argument("--model", choices=("spark", "glimmer"), default="spark")
    p.add_argument("--sim-url", default="http://127.0.0.1:8766")
    p.add_argument("--repl", action="store_true")
    a = p.parse_args()
    if not a.command and not a.repl:
        p.error("give a command or --repl")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))
    from sim_env.client import SimulationClient

    log = CallLog()
    runtime = Runtime(make_model(a.model, log), SimulationClient(a.sim_url), log=log,
                      say=lambda t: print(f"  SAYS: {t}"))
    commands = [" ".join(a.command)] if a.command else []
    while True:
        if not commands:
            try:
                text = input("command (or 'reset', 'quit')> ").strip()
            except EOFError:
                break
            if text in ("quit", "exit"):
                break
            if text == "reset":
                runtime.reset()
                print("  reset")
                continue
            commands = [text] if text else []
            continue
        text = commands.pop(0)
        since = len(runtime.timeline())
        print(f"> {text}")
        print_result(runtime, runtime.command(text), since)
        if not a.repl:
            break


if __name__ == "__main__":
    main()
