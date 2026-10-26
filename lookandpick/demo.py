"""Sim demo: pick a cube, with a hand appearing mid-run."""
from .adapters.fake import FakeTracker, SimArm
from .safety import Safety
from .tools import Tools
from .world import Hand, WorldState


def main() -> None:
    tracker, arm = FakeTracker(), SimArm()
    tools = Tools(WorldState(), tracker, arm, Safety())
    print(tools.get_scene())
    print(tools.pick("obj1"))
    tracker.set_hands([Hand("h1", (0.20, -0.15, 0.05))])
    print(tools.place("obj1", (0.20, -0.15, 0.0)))
    tools.stop()
    print(tools.pick("obj2"))


if __name__ == "__main__":
    main()
