"""Puts the pieces together: a model (Muse Spark or Glimmer) -> Orchestrator -> Tools/Safety -> arm and tracker.

    python -m lookandpick.runtime "pick up the red cube and put it at 0.18 -0.2"
    python -m lookandpick.runtime --repl --model glimmer
    python -m lookandpick.runtime --scene sam "pick up the red cube"     # Spark names what is on the table, SAM 3.1 finds it
    ~/src/hands-venv/bin/python -m lookandpick.runtime --hand-gate 2 --marker-mm 120 --intrinsics calibration/ace-pro.json "pick up the red cube"
        # the live hand gate on a real camera in front of the simulated arm (needs the hands environment; --status-port serves Deck)

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
from .models.orchestrator import STOP_WORDS, Orchestrator, RunResult
from .safety import Safety
from .scene import SceneUnavailable
from .tools import Tools
from .world import WorldState


class Runtime:
    def __init__(self, model: ChatModel, arm=None, tracker=None, log: CallLog | None = None,
                 say=print, safety: Safety | None = None, scene=None, gate=None) -> None:
        if arm is None:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))
            from sim_env.client import SimulationClient  # noqa: PLC0415
            arm = SimulationClient()
        self.arm, self.tracker, self.scene = arm, tracker or arm, scene   # `scene.refresh()` looks at the table before each command
        self.gate = gate                                                  # a HandGate: the live hand loop; pass its `arm`, `tracker` and `safety` too
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
        # A stop word never waits for anything. Otherwise look at the table first, and if it cannot be seen, do not move.
        if self.scene is not None and not STOP_WORDS.match(text):
            try:
                self.scene.refresh()
            except SceneUnavailable as exc:
                self._speak("I can't see the table right now, so I won't move.")
                return RunResult(text, reply="I can't see the table right now, so I won't move.", error=f"scene unavailable: {exc}")
        return self.orchestrator.run(text)

    def stop(self) -> str:
        return self.orchestrator.stop()

    def hand_gate_status(self) -> dict | None:
        return None if self.gate is None else self.gate.status()

    def reset(self) -> None:
        """Clear the stop latch and put the simulation back. An explicit operator action, never on the safety path.
        With a hand gate this builds a fresh gate, and no motion is allowed until it has a fresh observation."""
        if self.gate is not None:
            self.gate.reset()                       # also clears the shared safety latch
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
    for e in entries:
        if e["source"] == "SAM 3.1":
            print(f"  SAM 3.1: {e['input']} -> {e['output']} ({e['latency_ms'] / 1000:.1f} s)")
    calls = [e for e in entries if e["source"] in ("Muse Spark", "Muse Glimmer")]
    if calls:
        print(f"  model: {len(calls)} calls, {sum(e['latency_ms'] for e in calls) / 1000:.1f} s, "
              f"${sum(e['cost_usd'] for e in calls):.4f}")


def sam_scene_from_sim(model, log: CallLog, sim_url: str, camera: str = "camera_a", worker_url: str | None = None,
                       object_height_m: float = 0.036, marker_m: float = 0.12):
    """Language to target on the simulator: a SamScene looking through a sim camera, calibrated from that frame's own ArUco marker
    (not from the simulator's camera pose). Returns (scene, tracker). Needs scripts/sam_worker.py running in the SAM environment."""
    import json
    import urllib.request

    import cv2
    import numpy as np

    from .adapters.sam_client import DEFAULT_URL, SamClient
    from .adapters.sam_tracker import SamTracker
    from .calibration import TableCalibration, detect_marker
    from .scene import SamScene
    from .scene_labeller import SceneLabeller

    base = sim_url.rstrip("/")

    def grab():
        try:
            with urllib.request.urlopen(f"{base}/api/cameras/{camera}/rgb.jpg", timeout=5) as reply:
                return cv2.imdecode(np.frombuffer(reply.read(), np.uint8), cv2.IMREAD_COLOR)
        except OSError:
            return None

    frame = grab()
    if frame is None:
        raise SystemExit(f"cannot read camera {camera} from {base}; is the simulation running with rendering on?")
    with urllib.request.urlopen(f"{base}/api/cameras/{camera}/metadata", timeout=5) as reply:
        K = np.array(json.load(reply)["K"])
    corners = detect_marker(frame)
    if corners is None:
        raise SystemExit(f"the marker is not visible from {camera}; choose another camera")
    calibration = TableCalibration.from_marker(corners, marker_m, K)
    height, width = frame.shape[:2]
    tracker = SamTracker(SamClient(worker_url or DEFAULT_URL, timeout_s=60), calibration, (width, height), object_height_m=object_height_m,
                         workspace=((-0.4, 0.5), (-0.4, 0.4)))
    return SamScene(grab, SceneLabeller(model), tracker, log), tracker


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="*", help="what to ask the arm to do")
    p.add_argument("--model", choices=("spark", "glimmer"), default="spark")
    p.add_argument("--sim-url", default="http://127.0.0.1:8766")
    p.add_argument("--repl", action="store_true")
    p.add_argument("--scene", choices=("truth", "sam"), default="truth",
                   help="'truth': the simulator's own object list (default). 'sam': Spark names the objects from a camera frame and SAM 3.1 finds them")
    p.add_argument("--camera", default="camera_a", help="simulator camera for --scene sam")
    p.add_argument("--worker-url", help="SAM worker address for --scene sam (default http://127.0.0.1:8767)")
    p.add_argument("--hand-gate", type=int, metavar="CAM", help="run the live hand gate on this camera index; needs the hands environment")
    p.add_argument("--hand-model", default="weights/hand_landmarker.task", help="MediaPipe hand_landmarker.task")
    p.add_argument("--marker-mm", type=float, default=120.0, help="black square of the ArUco marker, for the hand-gate calibration")
    p.add_argument("--intrinsics", help="lens file for the hand-gate camera (calibration/<name>.json); without it the lens is guessed")
    p.add_argument("--hand-height", type=float, default=0.08, help="assumed height of hands above the table, metres")
    p.add_argument("--gate-width", type=int, default=640)
    p.add_argument("--gate-height", type=int, default=360)
    p.add_argument("--status-port", type=int, help="serve /api/hand-gate and /api/timeline (read-only, loopback) on this port for Deck")
    a = p.parse_args()
    if not a.command and not a.repl:
        p.error("give a command or --repl")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))
    from sim_env.client import SimulationClient

    log = CallLog()
    model = make_model(a.model, log)
    arm = SimulationClient(a.sim_url)
    scene = tracker = None
    if a.scene == "sam":
        scene, tracker = sam_scene_from_sim(model, log, a.sim_url, a.camera, a.worker_url)
    gate = None
    if a.hand_gate is not None:
        from .hand_gate import live_hand_gate  # noqa: PLC0415
        gate = live_hand_gate(arm, a.hand_gate, a.hand_model, marker_mm=a.marker_mm, intrinsics=a.intrinsics, hand_height=a.hand_height,
                              width=a.gate_width, height=a.gate_height)
        gate.start()
        print("Hand gate running: the arm only moves while the camera sees no hand near its path.")
        runtime = Runtime(model, gate.arm, tracker=gate.tracker(tracker or arm), log=log, say=lambda t: print(f"  SAYS: {t}"),
                          safety=gate.safety, scene=scene, gate=gate)
    else:
        runtime = Runtime(model, arm, tracker=tracker, log=log, say=lambda t: print(f"  SAYS: {t}"), scene=scene)
    if a.status_port:
        from .status_server import serve  # noqa: PLC0415
        serve(runtime.hand_gate_status, runtime.timeline, a.status_port)
        print(f"Status for Deck: http://127.0.0.1:{a.status_port}/api/hand-gate and /api/timeline")
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
    if gate is not None:
        gate.close()


if __name__ == "__main__":
    main()
