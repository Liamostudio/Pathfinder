"""Send one real request through Muse Spark and the constrained orchestrator, against fake adapters.

    python scripts/spark_smoke.py "pick up the apple"

Costs a fraction of a cent. Reads MODEL_API_KEY from .env (never printed). The arm is a pure
simulation here; nothing physical is involved.
"""
import sys

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.models.calllog import CallLog
from lookandpick.models.meta import MetaClient
from lookandpick.models.orchestrator import Orchestrator
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import TrackedObject, WorldState


def main() -> None:
    command = " ".join(sys.argv[1:]) or "pick up the apple"
    objects = [TrackedObject("obj1", "red mug", (0.20, 0.10, 0.04)), TrackedObject("obj2", "green apple", (0.10, -0.15, 0.03))]
    tracker, arm = FakeTracker(objects), SimArm()
    said: list[str] = []
    log = CallLog()
    result = Orchestrator(MetaClient.from_env(log), Tools(WorldState(), tracker, arm, Safety(), say=said.append)).run(command)
    print("command:", command)
    for step in result.steps:
        print("  tool:", step["name"], step["args"], "->", step["result"])
    print("said:", said, "| reply:", result.reply, "| error:", result.error or "none")
    for e in log.entries():
        print(f"  model call: {e['latency_ms']:.0f} ms, ${e['cost_usd']:.4f}, tokens {e['tokens']}, {e['status']}")


if __name__ == "__main__":
    main()
