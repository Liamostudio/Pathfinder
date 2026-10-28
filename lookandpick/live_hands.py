"""Live camera hand-gate: fresh frames -> hand detector -> table position -> speed gate on an arm.

    python -m lookandpick.live_hands --cam 2 --model weights/hand_landmarker.task --marker-mm 120 \
        --intrinsics calibration/ace-pro.json --arm sim

The arm is the fake `SimArm` or the MuJoCo simulation over its bridge (`--arm sim`, start
`simulation/start.py` first). It never opens a serial port or the real arm. A patrol thread moves the arm
back and forth over the table so a hand entering the path visibly slows and stops it. The watchdog fails
safe (latched stop) if the camera stalls or the detector dies. Each frame's timings go to a JSONL log in
`recordings/`, and a summary is printed at the end. Needs the `vision` and `hands` extras.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .adapters.base import HandDetector
from .adapters.fake import SimArm
from .adapters.live_camera import LatestFrameCamera
from .calibration import TableCalibration, detect_marker
from .hand_safety import HandDetectionLoop, HandSafetyMonitor
from .intrinsics import Intrinsics
from .safety import Safety
from .world import Vec3, WorldState


class CalibrationError(RuntimeError):
    pass


def guess_intrinsics(width: int, height: int, hfov_deg: float) -> np.ndarray:
    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1.0]])


def calibrate_from_frames(frames, marker_mm: float, camera_matrix, dist=None, *, marker_id: int = 0,
                          want: int = 12, max_frames: int = 90, max_reproj_px: float = 3.0) -> TableCalibration:
    """Median marker corners over several frames, so one noisy detection can't set the table frame."""
    found = []
    for i, (_, frame) in enumerate(frames):
        corners = detect_marker(frame, marker_id)
        if corners is not None:
            found.append(corners)
        if len(found) >= want or i + 1 >= max_frames:
            break
    if len(found) < max(3, want // 2):
        raise CalibrationError(f"marker id {marker_id} seen in only {len(found)} frames; keep the marker panel in view and unobstructed")
    cal = TableCalibration.from_marker(np.median(np.stack(found), axis=0), marker_mm / 1000, camera_matrix, dist)
    if cal.reprojection_px > max_reproj_px:
        raise CalibrationError(f"marker fit is poor ({cal.reprojection_px:.1f} px); check the lens calibration and the marker size")
    return cal


class TappedDetector:
    """Passes detections through unchanged, keeping the latest for the on-screen overlay."""

    def __init__(self, inner: HandDetector) -> None:
        self.inner, self.last = inner, []

    def detect(self, frame, timestamp_ms):
        self.last = self.inner.detect(frame, timestamp_ms)
        return self.last

    def close(self):
        self.inner.close()


class TimedMonitor(HandSafetyMonitor):
    """Records, per observation, how long it took from the start of processing to the gate being applied."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []
        self.frame_age_ms = 0.0
        self.log_file = None

    def publish(self, hands, captured_at, inference_ms=0):
        super().publish(hands, captured_at, inference_ms)
        row = {"t": round(time.monotonic(), 4), "frame_age_ms": round(self.frame_age_ms, 2),
               "inference_ms": round(inference_ms, 2), "decision_ms": round((time.monotonic() - captured_at) * 1000, 2),
               "hands": len(hands), "scale": round(self.scale, 3), "reason": self.reason}
        self.records.append(row)
        if self.log_file:
            self.log_file.write(json.dumps(row) + "\n")


class Patrol(threading.Thread):
    """Sweeps the arm between two points; the monitor's speed scale decides how fast it really goes."""

    def __init__(self, arm, a: Vec3, b: Vec3) -> None:
        super().__init__(name="patrol", daemon=True)
        self.arm, self.goal, self._points, self._stop = arm, b, (a, b), threading.Event()

    def run(self):
        i = 0
        while not self._stop.is_set():
            self.goal = self._points[i % 2]
            ok = self.arm.move_to(self.goal, 1.0)
            if ok:
                i += 1
            self._stop.wait(0.05 if ok else 0.2)

    def close(self):
        self._stop.set()


def _pct(values, p):
    return round(sorted(values)[max(0, math.ceil(p * len(values)) - 1)], 2) if values else None


def summarise(records: list[dict], seconds: float) -> dict:
    stats = {"frames": len(records), "seconds": round(seconds, 2),
             "effective_fps": round(len(records) / seconds, 1) if seconds > 0 else 0}
    for key in ("inference_ms", "decision_ms", "frame_age_ms"):
        vals = [r[key] for r in records]
        stats[key] = {"mean": round(statistics.mean(vals), 2), "p95": _pct(vals, 0.95), "max": max(vals)} if vals else None
    stats["frames_with_hands"] = sum(r["hands"] > 0 for r in records)
    stats["min_scale"] = min((r["scale"] for r in records), default=None)
    stats["slow_or_stop_frames"] = sum(r["scale"] < 1 for r in records)
    return stats


def run_live(camera, detector: HandDetector, calibration: TableCalibration, arm, *, hand_height: float = 0.08,
             seconds: float | None = None, show=None, log_path: Path | None = None, hz: float = 30.0,
             timeout_ms: float = 150.0, patrol_points: tuple[Vec3, Vec3] = ((-0.15, 0.10, 0.05), (0.15, 0.10, 0.05)),
             table_to_arm=None) -> dict:
    """Run until `seconds` elapse (or `show` returns False). Returns timing statistics. `show(state)` draws a frame."""
    tapped = TappedDetector(detector)
    patrol = Patrol(arm, *patrol_points)
    monitor = TimedMonitor(WorldState(), Safety(), arm, lambda: (arm.position, patrol.goal), timeout_s=timeout_ms / 1000)

    class AgeAwareSource:
        """Wraps the camera so the monitor knows how stale each frame was when it was picked up."""

        def frames(self_inner):
            for stamp, frame in camera.frames():
                monitor.frame_age_ms = (time.monotonic() - stamp) * 1000
                yield stamp, frame

    loop = HandDetectionLoop(AgeAwareSource(), tapped, calibration, monitor, hand_height=hand_height,
                             target_hz=hz, table_to_arm=table_to_arm)
    log = open(log_path, "w") if log_path else None
    monitor.log_file = log
    started = time.monotonic()
    try:
        loop.start()
        patrol.start()
        while loop._thread.is_alive() and (seconds is None or time.monotonic() - started < seconds):
            if show is not None and show({"camera": camera, "hands": tapped.last, "monitor": monitor, "patrol": patrol,
                                          "calibration": calibration, "loop": loop}) is False:
                break
            time.sleep(0.02 if show is not None else 0.1)
        final_reason, was_stopped = monitor.reason, monitor.safety.stopped  # before shutdown latches a stop
    finally:
        patrol.close()
        loop.close()
        camera.close()
        if log:
            log.close()
    summary = summarise(monitor.records, time.monotonic() - started)
    summary.update(final_reason=final_reason, stopped=was_stopped, mode="live camera, simulated arm only")
    return summary


def make_overlay(state: dict):
    import cv2  # noqa: PLC0415
    latest = state["camera"].latest()
    if latest is None:
        return None
    _, frame, _ = latest
    out = frame.copy()
    cal, monitor, patrol = state["calibration"], state["monitor"], state["patrol"]
    pts = [cal.table_to_pixel(p[:2], p[2]) for p in patrol._points]
    cv2.line(out, tuple(int(v) for v in pts[0]), tuple(int(v) for v in pts[1]), (255, 160, 0), 3)
    for hand in state["hands"]:
        for x, y in hand.pixels:
            cv2.circle(out, (int(x), int(y)), 3, (0, 255, 255), -1)
    snap = monitor.snapshot()
    scale = snap["speed_scale"]
    colour = (0, 160, 0) if scale >= 1 else (0, 160, 255) if scale > 0 else (0, 0, 220)
    label = "CLEAR" if scale >= 1 else f"SLOW x{scale:.2f}" if scale > 0 else "STOP"
    cv2.rectangle(out, (0, 0), (out.shape[1], 46), colour, -1)
    cv2.putText(out, f"{label}  {snap['reason']}  inference {snap['inference_ms'] or 0:.0f} ms  (q quit, space stop)",
                (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return out


def make_arm(kind: str):
    if kind == "fake":
        return SimArm(start=(0.0, 0.10, 0.05))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))
    from sim_env.client import SimulationClient  # noqa: PLC0415
    return SimulationClient()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--model", type=Path, required=True, help="MediaPipe hand_landmarker.task")
    p.add_argument("--marker-mm", type=float, required=True)
    p.add_argument("--marker-id", type=int, default=0)
    p.add_argument("--intrinsics", type=Path, help="calibration/<name>.json; without it the lens is guessed (--hfov)")
    p.add_argument("--hfov", type=float, default=70.0)
    p.add_argument("--hand-height", type=float, default=0.08, help="assumed height of hands above the table, metres")
    p.add_argument("--arm", choices=("fake", "sim"), default="fake")
    p.add_argument("--seconds", type=float)
    p.add_argument("--no-window", action="store_true")
    p.add_argument("--hz", type=float, default=30.0)
    p.add_argument("--timeout-ms", type=float, default=150.0)
    a = p.parse_args()

    from .adapters.hands import MediaPipeHandDetector
    camera = LatestFrameCamera(a.cam, f"cam{a.cam}", a.width, a.height)
    lens = Intrinsics.load(a.intrinsics) if a.intrinsics else None
    if lens is not None and lens.size != (a.width, a.height):
        raise SystemExit(f"{a.intrinsics} was calibrated at {lens.size[0]}x{lens.size[1]}; use --width/--height to match")
    k, dist = (lens.camera_matrix, lens.dist) if lens else (guess_intrinsics(a.width, a.height, a.hfov), None)
    if lens is None:
        print("WARNING: lens not calibrated; hand positions are only roughly right (run scripts/calibrate_lens.py).", flush=True)
    print("Calibrating from the marker (keep your hands out of view for a moment)...", flush=True)
    frames = camera.frames()
    try:
        calibration = calibrate_from_frames(frames, a.marker_mm, k, dist, marker_id=a.marker_id)
    except CalibrationError as e:
        camera.close()
        raise SystemExit(f"Calibration failed: {e}")
    finally:
        frames.close()
    print(f"Calibrated: reprojection {calibration.reprojection_px:.2f} px, camera at {np.round(calibration.camera_position() * 100, 1)} cm", flush=True)

    show = None
    if not a.no_window:
        import cv2  # noqa: PLC0415
        cv2.namedWindow("live hand gate")

        def show(state):
            frame = make_overlay(state)
            if frame is not None:
                cv2.imshow("live hand gate", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                state["monitor"].safety.stop()
            return key != ord("q")

    log_dir = Path("recordings")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"live-hands-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    summary = run_live(camera, MediaPipeHandDetector(a.model), calibration, make_arm(a.arm), hand_height=a.hand_height,
                       seconds=a.seconds, show=show, log_path=log_path, hz=a.hz, timeout_ms=a.timeout_ms)
    print(json.dumps(summary, indent=2))
    print(f"Per-frame log: {log_path}")


if __name__ == "__main__":
    main()
