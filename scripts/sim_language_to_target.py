"""Acceptance test for language -> target on the simulator: does 'red cube' become the red cube's true table position?

  PYTHONPATH=. simulation/.venv/bin/python scripts/sim_language_to_target.py --camera camera_a --prompts "red cube,blue cube"

Needs the SAM worker running (scripts/sam_worker.py, in the SAM environment). It renders a simulator camera frame, calibrates the
camera from the frame's own ArUco marker exactly as the real pipeline does (no use of the simulator's camera pose), asks SAM 3.1 for each
prompt, turns the mask centres into marker-frame positions with `SamTracker`, and compares them with the simulator's ground truth.
The simulator draws flat-coloured cubes, so this proves the geometry and plumbing, not SAM's accuracy on real objects.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "simulation")]

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from lookandpick.adapters.sam_client import SamClient  # noqa: E402
from lookandpick.adapters.sam_tracker import SamTracker  # noqa: E402
from lookandpick.calibration import TableCalibration, detect_marker  # noqa: E402

CUBE_M = 0.036            # the simulator's cubes are 3.6 cm
MARKER_M = 0.12           # the black square of the simulator's marker texture


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default="camera_a", choices=["camera_a", "camera_b", "top", "orbit"])
    ap.add_argument("--prompts", default="red cube,blue cube")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--tolerance-mm", type=float, default=10.0)
    ap.add_argument("--save", type=Path, help="write an annotated picture here")
    a = ap.parse_args(argv)

    from sim_env.cameras import Cameras
    from sim_env.engine import Simulation
    sim = Simulation(realtime=False)
    cameras = Cameras(sim, width=a.width, height=a.height)
    try:
        if not cameras.ready.wait(30) or cameras.error:
            raise SystemExit(f"simulator camera failed: {cameras.error}")
        frame = cameras.frame(a.camera)
        bgr = cv2.cvtColor(frame["rgb"], cv2.COLOR_RGB2BGR)
        K = np.array(frame["metadata"]["K"])
        corners = detect_marker(bgr)
        if corners is None:
            raise SystemExit("the marker is not visible from this camera; try another with --camera")
        calibration = TableCalibration.from_marker(corners, MARKER_M, K)
        truth = {o.id: o for o in sim.objects()}
        print(f"camera {a.camera} {a.width}x{a.height}, marker reprojection {calibration.reprojection_px:.2f} px")
        client = SamClient(timeout_s=60)
        print("SAM worker:", client.health())
        tracker = SamTracker(client, calibration, (a.width, a.height), object_height_m=CUBE_M, workspace=((-0.4, 0.5), (-0.4, 0.4)))
        prompts = [p.strip() for p in a.prompts.split(",") if p.strip()]
        started = time.perf_counter()
        found = tracker.refresh(bgr, prompts)
        print(f"{len(found)} objects from {len(prompts)} prompts in {time.perf_counter() - started:.1f} s")
        names = {"red cube": "obj1", "blue cube": "obj2"}
        ok = True
        for obj in found:
            real = truth.get(names.get(obj.label, ""))
            if real is None:
                print(f"  {obj.id}: ({obj.position[0]*1000:+.0f}, {obj.position[1]*1000:+.0f}) mm (no ground truth for this label)")
                continue
            error = math.dist(obj.position[:2], real.position[:2]) * 1000
            ok &= error <= a.tolerance_mm
            print(f"  {obj.id}: SAM ({obj.position[0]*1000:+.0f}, {obj.position[1]*1000:+.0f}) mm   truth ({real.position[0]*1000:+.0f}, "
                  f"{real.position[1]*1000:+.0f}) mm   error {error:.1f} mm  {'OK' if error <= a.tolerance_mm else 'TOO FAR'}")
        missing = [p for p in prompts if p in names and not any(o.label == p for o in found)]
        if missing:
            ok = False
            print("  NOT FOUND:", ", ".join(missing))
        if a.save:
            note = bgr.copy()
            for prompt, detections in tracker.last_detections.items():
                for d in detections:
                    cv2.rectangle(note, tuple(int(v) for v in d.box[:2]), tuple(int(v) for v in d.box[2:]), (0, 255, 0), 2)
                    cv2.putText(note, f"{prompt} {d.score:.2f}", (int(d.box[0]), max(14, int(d.box[1]) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imwrite(str(a.save), note)
            print("saved", a.save)
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        cameras.close()


if __name__ == "__main__":
    raise SystemExit(main())
