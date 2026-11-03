"""Watch the HandGate work on a real camera: a simulated arm patrols over the marker; put a hand in its path.

  PYTHONPATH=. ~/src/hands-venv/bin/python scripts/hand_gate_demo.py --cam 2 --intrinsics calibration/ace-pro.json

Needs the simulation bridge (simulation/start.py, 127.0.0.1:8766) and the hands environment. A window shows the camera, the arm's patrol line
projected onto the table, the hand landmarks, and the gate's state. The arm is SIMULATED: nothing here can move the real arm.
q quits, space stops (latches, like STOP), r resets after a stop. A timing summary is printed at the end.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "simulation")]

import cv2  # noqa: E402

from lookandpick.hand_gate import live_hand_gate  # noqa: E402
from lookandpick.live_hands import Patrol, TappedDetector  # noqa: E402

COLOURS = {"clear": (0, 160, 0), "slow": (0, 160, 255), "stop": (0, 0, 220), "latched": (0, 0, 120), "stale": (160, 0, 160)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cam", type=int, default=2)
    ap.add_argument("--model", default="weights/hand_landmarker.task")
    ap.add_argument("--intrinsics", default="calibration/ace-pro.json")
    ap.add_argument("--marker-mm", type=float, default=120.0)
    ap.add_argument("--hand-height", type=float, default=0.08)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--patrol", default="-0.15,0.10,0.05,0.15,0.10,0.05", help="x1,y1,z1,x2,y2,z2 in the marker frame, metres")
    a = ap.parse_args()
    from sim_env.client import SimulationClient
    v = [float(x) for x in a.patrol.split(",")]
    taps: list[TappedDetector] = []

    def tap(inner):
        taps.append(TappedDetector(inner))
        return taps[-1]

    gate = live_hand_gate(SimulationClient(), a.cam, a.model, marker_mm=a.marker_mm, intrinsics=a.intrinsics, hand_height=a.hand_height, wrap_detector=tap)
    gate.start()
    patrol = Patrol(gate.arm, tuple(v[:3]), tuple(v[3:]))
    patrol.start()
    log, started, latches, manual = [], time.monotonic(), [], []
    sim = SimulationClient()
    print("Running. Put a hand into the arm's patrol line (the orange line in the window).", flush=True)
    try:
        while time.monotonic() - started < a.seconds:
            status = gate.status()
            log.append((time.monotonic() - started, status["state"], status["scale"], status["nearest_m"], status["fps"], status["latency_ms"]["p95"]))
            if status["state"] == "latched" and (not latches or latches[-1][1] != status["reason"] or len(log) < 2 or log[-2][1] != "latched"):
                latches.append((round(time.monotonic() - started, 1), status["reason"], "pressed space" if manual and manual[-1] >= time.monotonic() - started - 1 else "not by a key press"))
            if not a.no_window:
                latest = gate.camera.latest()
                if latest is not None:
                    frame = latest[1].copy()
                    cal = gate.calibration
                    p1, p2 = (tuple(int(c) for c in cal.table_to_pixel(tuple(v[i:i + 2]), v[i + 2])) for i in (0, 3))
                    cv2.line(frame, p1, p2, (255, 160, 0), 3)
                    for det in (taps[-1].last if taps else []):
                        for x, y in det.pixels:
                            cv2.circle(frame, (int(x), int(y)), 2, (0, 255, 255), -1)
                    cv2.rectangle(frame, (0, 0), (frame.shape[1], 30), COLOURS[status["state"]], -1)
                    near = "" if status["nearest_m"] is None else f"  nearest {status['nearest_m'] * 100:.0f} cm"
                    cv2.putText(frame, f"{status['state'].upper()}  x{status['scale']:.2f}{near}  {status['fps']:.0f} fps  p95 {status['latency_ms']['p95']} ms",
                                (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                    cv2.imshow("hand gate (q quit, space stop, r reset)", frame)
                key = cv2.waitKey(30) & 0xFF
                if key == ord("q"):
                    break
                if key == ord(" "):
                    manual.append(time.monotonic() - started)
                    gate.safety.stop()
                if key == ord("r"):
                    gate.reset()
                    sim.action("reset")               # the simulated arm latches its own halt; put it back too
            else:
                time.sleep(0.03)
    finally:
        patrol.close()
        gate.close()
        cv2.destroyAllWindows()
    states = [s for _, s, *_ in log]
    nearest = [x[3] for x in log if x[3] is not None]
    summary = {"seconds": round(time.monotonic() - started, 1), "samples": len(log), **{f"time_{k}_pct": round(100 * states.count(k) / max(1, len(states))) for k in COLOURS},
               "min_scale": min((x[2] for x in log), default=None), "min_nearest_m": min(nearest, default=None),
               "fps_median": statistics.median(x[4] for x in log if x[4]) if any(x[4] for x in log) else None,
               "p95_decision_ms": max((x[5] for x in log if x[5] is not None), default=None), "latches": latches}
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
