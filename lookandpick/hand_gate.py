"""HandGate: the live hand loop assembled behind the runtime, with a status Deck can show.

    camera -> hand detector -> table position -> speed gate (watchdog) -> the arm's speed scale, and a latched stop

It is the ONLY safety path for people near the arm: local, no network, a 150 ms watchdog that latches a stop if the camera or detector stalls.
The pieces are the existing, tested ones (`HandDetectionLoop`, `HandSafetyMonitor` via `TimedMonitor`); this module wires them to the same
`Safety` latch the runtime's STOP uses, remembers where the current move is heading so the watchdog watches the right segment, and reports state.

    gate = HandGate(camera, make_detector, calibration, arm)
    gate.start()
    runtime = Runtime(model, gate.arm, tracker=gate.tracker(objects), safety=gate.safety)

A stop latches and never resumes by itself. `reset()` is an explicit operator action that builds a fresh monitor and detector and demands a
fresh observation before any motion is allowed. If the gate is not running, `hands()` reports a huge hand at the arm, so every move is refused.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from .hand_safety import HandDetectionLoop
from .live_hands import TimedMonitor
from .safety import NO_GATE_ID, Safety, distance
from .world import Hand, Vec3, WorldState

NO_GATE_HAND = Hand(NO_GATE_ID, (0.0, 0.0, 0.0), 10.0)       # fail closed: a hand everywhere


class TargetTrackingArm:
    """Wraps an arm to remember where its current move is heading, so the watchdog monitors the segment that is really moving."""

    def __init__(self, arm) -> None:
        self._arm, self.goal = arm, None

    @property
    def position(self) -> Vec3:
        return self._arm.position

    def move_to(self, target: Vec3, speed_scale: float) -> bool:
        self.goal = target
        try:
            return self._arm.move_to(target, speed_scale)
        finally:
            self.goal = None

    def grip(self, closed: bool) -> bool:
        return self._arm.grip(closed)

    def halt(self) -> None:
        self._arm.halt()

    def set_speed_scale(self, scale: float) -> None:
        self._arm.set_speed_scale(scale)

    def path(self) -> tuple[Vec3, Vec3]:
        here = self._arm.position
        return (here, self.goal if self.goal is not None else here)

    def __getattr__(self, name):                # actions, state, anything else the wrapped arm offers
        return getattr(self._arm, name)


class GateMonitor(TimedMonitor):
    """TimedMonitor that keeps only the recent timings, so a gate left running for hours does not grow without bound."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = deque(maxlen=600)


class GateTracker:
    """Objects from one tracker, hands only from the gate's current monitor (never another tracker's empty list)."""

    def __init__(self, gate: "HandGate", objects_tracker) -> None:
        self.gate, self.objects_tracker = gate, objects_tracker

    def objects(self):
        return self.objects_tracker.objects()

    def hands(self) -> list[Hand]:
        monitor = self.gate.monitor
        if monitor is None:
            return [NO_GATE_HAND]
        monitor.tick()
        return [Hand(**hand) for hand in monitor.world.snapshot()["hands"]]


@dataclass(frozen=True)
class CameraSpec:
    """One extra camera for the gate: its own detector and its own calibration into the SAME marker frame."""
    name: str
    camera: object
    make_detector: Callable
    calibration: object


class HandFusion:
    """Combine several cameras' hands into ONE observation for the single watchdog.

    Each camera publishes its latest hands (already in the shared marker frame). The combined observation is the UNION of every camera's
    newest hands, stamped with the OLDEST of the cameras' capture times. So the decision is never newer than the slowest camera, and if any one
    camera stalls the combined stamp stops advancing and the watchdog latches a stop: losing a camera stops the arm (fail safe).
    A combined observation is only passed on when its stamp is newer than the last one, which keeps the monitor's increasing-timestamp rule."""

    def __init__(self, monitor) -> None:
        self.monitor, self._latest, self._last, self._lock, self._started = monitor, {}, -math.inf, threading.Lock(), False

    def publish(self, name: str, hands: list[Hand], captured_at: float, inference_ms: float) -> None:
        with self._lock:
            if name in self._latest and captured_at <= self._latest[name][0]:
                return                                      # a camera never goes backwards: an older frame than its last is ignored
            self._latest[name] = (captured_at, [replace(h, id=f"{name}:{h.id}") for h in hands], inference_ms)
            stamp = min(c for c, _, _ in self._latest.values())
            if stamp <= self._last:
                return
            self._last = stamp
            union = [h for _, hs, _ in self._latest.values() for h in hs]
            inference = max(i for _, _, i in self._latest.values())
            self.monitor.publish(union, stamp, inference)

    def start(self) -> None:
        with self._lock:
            if not self._started:
                self._started = True
                self.monitor.start()

    def cameras(self) -> dict:
        now = time.monotonic()
        with self._lock:
            return {n: {"age_s": round(now - c, 3), "hands": len(hs)} for n, (c, hs, _) in self._latest.items()}


class CameraPort:
    """What one camera's HandDetectionLoop sees as 'the monitor': publishes go to the fusion, everything safety-related goes straight through."""

    def __init__(self, fusion: HandFusion, name: str) -> None:
        self.fusion, self.name = fusion, name

    @property
    def safety(self):
        return self.fusion.monitor.safety

    def publish(self, hands, captured_at, inference_ms=0):
        self.fusion.publish(self.name, hands, captured_at, inference_ms)

    def start(self):
        self.fusion.start()

    def fail(self, reason):
        self.fusion.monitor.fail(f"{self.name}: {reason}")

    def close(self):
        self.fusion.monitor.close()

    def __getattr__(self, attr):
        return getattr(self.fusion.monitor, attr)


def _percentile(values: list[float], p: float):
    return round(sorted(values)[max(0, math.ceil(p * len(values)) - 1)], 1) if values else None


class HandGate:
    def __init__(self, camera, make_detector, calibration, arm, *, safety: Safety | None = None, hand_height: float = 0.08,
                 timeout_s: float = 0.15, hz: float = 30.0, warmup_s: float = 10.0, table_to_arm=None, clock=time.monotonic,
                 extra_cameras: list[CameraSpec] | tuple = ()) -> None:
        """One camera, or the main camera plus `extra_cameras` (each with its own calibration into the same marker frame). A hand seen by ANY camera
        slows the arm; if ANY camera stalls, the watchdog latches a stop."""
        self.camera, self.make_detector, self.calibration = camera, make_detector, calibration
        self.extra_cameras = list(extra_cameras)
        self.arm, self.safety = TargetTrackingArm(arm), safety or Safety.for_arm(arm)
        self.hand_height, self.timeout_s, self.hz, self.warmup_s = hand_height, timeout_s, hz, warmup_s
        self.table_to_arm, self._clock = table_to_arm, clock
        self.monitor: GateMonitor | None = None
        self.loop: HandDetectionLoop | None = None
        self.loops: list[HandDetectionLoop] = []
        self.fusion: HandFusion | None = None
        self._started_cameras: list = []
        self._lock = threading.RLock()

    def _all(self) -> list[CameraSpec]:
        return [CameraSpec("main", self.camera, self.make_detector, self.calibration)] + self.extra_cameras

    # -- life cycle --------------------------------------------------------------------------------------------------
    def start(self, clear_latch: bool = False) -> None:
        """Warm up every camera first (a webcam can take seconds to deliver and the detector's first inference is slow, and neither may trip the
        short watchdog), then run. Raises RuntimeError if ANY camera never delivers: the gate is not running and moves are refused."""
        with self._lock:
            if self.loops:
                raise RuntimeError("the hand gate is already running")
            ready = []
            for spec in self._all():
                if spec.camera not in self._started_cameras:
                    spec.camera.start()
                    self._started_cameras.append(spec.camera)
                deadline = self._clock() + self.warmup_s
                latest = spec.camera.latest()
                while latest is None and self._clock() < deadline:
                    time.sleep(0.02)
                    latest = spec.camera.latest()
                if latest is None:
                    raise RuntimeError(f"the {spec.name} camera produced no frame within {self.warmup_s:.0f} s")
                detector = spec.make_detector()
                detector.detect(latest[1], int(time.monotonic() * 1000))      # first inference is slow: pay for it before the clock starts
                ready.append((spec, detector))
            if clear_latch:
                self.safety.reset()               # only now that every camera has delivered: a failed rebuild stays latched (and fails closed)
            self._build(ready)

    def _build(self, ready) -> None:
        monitor = GateMonitor(WorldState(), self.safety, self.arm, self.arm.path, timeout_s=self.timeout_s)
        self.monitor = monitor
        fusion = HandFusion(monitor) if len(ready) > 1 else None
        self.fusion = fusion
        loops = []
        for spec, detector in ready:
            camera = spec.camera

            class AgeAwareSource:
                def frames(_self, camera=camera):
                    for stamp, frame in camera.frames():
                        monitor.frame_age_ms = (time.monotonic() - stamp) * 1000
                        yield stamp, frame

            port = monitor if fusion is None else CameraPort(fusion, spec.name)
            loops.append(HandDetectionLoop(AgeAwareSource(), detector, spec.calibration, port, hand_height=self.hand_height,
                                           target_hz=self.hz, table_to_arm=self.table_to_arm))
        self.loops, self.loop = loops, loops[0]
        for loop in loops:
            loop.start()

    def reset(self) -> None:
        """Explicit operator action after a stop: a fresh monitor and detectors, and no motion until a fresh observation arrives."""
        with self._lock:
            self._shutdown_loop()
            self.start(clear_latch=True)

    def _shutdown_loop(self) -> None:
        loops, self.loops, self.loop, self.monitor, self.fusion = self.loops, [], None, None, None
        for loop in loops:
            loop.close()

    def close(self) -> None:
        with self._lock:
            self._shutdown_loop()
            for camera in self._started_cameras:
                camera.close()
            self._started_cameras = []

    def tracker(self, objects_tracker) -> GateTracker:
        return GateTracker(self, objects_tracker)

    # -- status for Deck ---------------------------------------------------------------------------------------------
    def status(self) -> dict:
        """The shape Deck renders: state, speed scale, nearest hand, fps, latency and how stale the last observation is."""
        monitor = self.monitor
        now = time.time()
        if monitor is None:
            return {"state": "stale" if not self.safety.stopped else "latched", "scale": 0.0, "nearest_m": None, "hands": 0, "fps": 0.0,
                    "latency_ms": {"p50": None, "p95": None}, "stale_s": None, "reason": "the hand gate is not running", "updated": now}
        snap = monitor.snapshot()
        records = list(monitor.records)
        recent = [r for r in records if time.monotonic() - r["t"] <= 2.0]
        hands = monitor.world.snapshot()["hands"]
        position = self.arm.position
        nearest = min((max(0.0, distance(tuple(h["position"]), position) - h["radius_m"]) for h in hands), default=None)
        age = None if snap["age_ms"] is None else snap["age_ms"] / 1000
        if self.safety.stopped:
            state = "latched"
        elif not snap["healthy"]:
            state = "stale"
        elif snap["speed_scale"] >= 1:
            state = "clear"
        elif snap["speed_scale"] > 0:
            state = "slow"
        else:
            state = "stop"
        decisions = [r["decision_ms"] for r in records[-100:]]
        out = {"state": state, "scale": round(snap["speed_scale"], 3), "nearest_m": None if nearest is None else round(nearest, 3),
                "hands": len(hands), "fps": round(len(recent) / 2.0, 1),
                "latency_ms": {"p50": _percentile(decisions, 0.5), "p95": _percentile(decisions, 0.95)},
                "stale_s": None if age is None else round(age, 3), "reason": snap["reason"], "updated": now}
        # Where the camera hands are (marker frame, metres), read-only: the real-arm hand-stop demo does its own distance maths with these.
        out["positions"] = [{"id": str(h["id"]), "position": [float(c) for c in h["position"]], "radius_m": float(h["radius_m"])} for h in hands]
        if self.fusion is not None:
            out["cameras"] = self.fusion.cameras()              # per camera: how old its newest frame is, and how many hands it sees
        return out


def _calibrated_camera(cam: int, intrinsics, *, marker_mm: float, marker_id: int, hfov_deg: float, width: int, height: int, name: str, log):
    """(camera, calibration) for one real camera, calibrated from the marker in view. Raises SystemExit with a plain message on failure."""
    from pathlib import Path

    from .adapters.live_camera import LatestFrameCamera
    from .intrinsics import Intrinsics
    from .live_hands import CalibrationError, calibrate_from_frames, guess_intrinsics

    lens = Intrinsics.load(Path(intrinsics)) if intrinsics else None
    if lens is not None and lens.size != (width, height):
        raise SystemExit(f"{intrinsics} was calibrated at {lens.size[0]}x{lens.size[1]}; run the gate at that size (--gate-width/--gate-height)")
    k, dist = (lens.camera_matrix, lens.dist) if lens else (guess_intrinsics(width, height, hfov_deg), None)
    if lens is None:
        log(f"WARNING: the {name} camera's lens is not calibrated; hand positions are only roughly right (run scripts/calibrate_lens.py).")
    log(f"Calibrating the {name} camera from the marker (keep your hands out of view for a moment)...")
    probe = LatestFrameCamera(cam, f"cam{cam}", width, height, stall_s=6)
    frames = probe.frames()
    try:
        calibration = calibrate_from_frames(frames, marker_mm, k, dist, marker_id=marker_id, want=8, max_frames=60)
    except CalibrationError as exc:
        raise SystemExit(f"Hand-gate calibration failed for the {name} camera: {exc}") from None
    finally:
        frames.close()
        probe.close()
    log(f"Calibrated the {name} camera: reprojection {calibration.reprojection_px:.2f} px at {width}x{height}")
    return LatestFrameCamera(cam, f"cam{cam}", width, height), calibration


def live_hand_gate(arm, cam: int, model_path, *, marker_mm: float = 120.0, marker_id: int = 0, intrinsics=None, hfov_deg: float = 70.0,
                   hand_height: float = 0.08, width: int = 640, height: int = 360, safety: Safety | None = None, log=print,
                   wrap_detector=None, second_cam: int | None = None, second_intrinsics=None,
                   second_size: tuple[int, int] = (1280, 720)) -> HandGate:
    """Real cameras and MediaPipe, each calibrated from the SAME ArUco marker (keep hands out of the picture for a moment).

    With `second_cam`, a second camera (its own lens file, calibrated and run at `second_size`) joins the same gate: a hand seen by either slows the arm
    and a stalled camera stops it.
    Calibrates and runs at the same resolution (USB 2 cameras are only fast at low resolution). Needs the hands environment
    (mediapipe, opencv). Raises SystemExit with a plain message if a marker cannot be seen or a lens file does not match."""
    from .adapters.hands import MediaPipeHandDetector

    def make():
        return MediaPipeHandDetector(model_path) if wrap_detector is None else wrap_detector(MediaPipeHandDetector(model_path))

    camera, calibration = _calibrated_camera(cam, intrinsics, marker_mm=marker_mm, marker_id=marker_id, hfov_deg=hfov_deg, width=width,
                                             height=height, name="main", log=log)
    extra = []
    if second_cam is not None:
        second, second_cal = _calibrated_camera(second_cam, second_intrinsics, marker_mm=marker_mm, marker_id=marker_id, hfov_deg=hfov_deg,
                                                width=second_size[0], height=second_size[1], name="second", log=log)
        extra.append(CameraSpec("second", second, lambda: MediaPipeHandDetector(model_path), second_cal))
    return HandGate(camera, make, calibration, arm, safety=safety, hand_height=hand_height, extra_cameras=extra)
