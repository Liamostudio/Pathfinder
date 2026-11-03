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

from .hand_safety import HandDetectionLoop
from .live_hands import TimedMonitor
from .safety import Safety, distance
from .world import Hand, Vec3, WorldState

NO_GATE_HAND = Hand("hand-gate-not-running", (0.0, 0.0, 0.0), 10.0)       # fail closed: a hand everywhere


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


def _percentile(values: list[float], p: float):
    return round(sorted(values)[max(0, math.ceil(p * len(values)) - 1)], 1) if values else None


class HandGate:
    def __init__(self, camera, make_detector, calibration, arm, *, safety: Safety | None = None, hand_height: float = 0.08,
                 timeout_s: float = 0.15, hz: float = 30.0, warmup_s: float = 10.0, table_to_arm=None, clock=time.monotonic) -> None:
        self.camera, self.make_detector, self.calibration = camera, make_detector, calibration
        self.arm, self.safety = TargetTrackingArm(arm), safety or Safety()
        self.hand_height, self.timeout_s, self.hz, self.warmup_s = hand_height, timeout_s, hz, warmup_s
        self.table_to_arm, self._clock = table_to_arm, clock
        self.monitor: GateMonitor | None = None
        self.loop: HandDetectionLoop | None = None
        self._camera_started = False
        self._lock = threading.RLock()

    # -- life cycle --------------------------------------------------------------------------------------------------
    def start(self) -> None:
        """Warm up first (a webcam can take seconds to deliver and the detector's first inference is slow, and neither may trip the short
        watchdog), then run. Raises RuntimeError if the camera never delivers: the gate is not running and moves are refused."""
        with self._lock:
            if self.loop is not None:
                raise RuntimeError("the hand gate is already running")
            if not self._camera_started:
                self.camera.start()
                self._camera_started = True
            deadline = self._clock() + self.warmup_s
            latest = self.camera.latest()
            while latest is None and self._clock() < deadline:
                time.sleep(0.02)
                latest = self.camera.latest()
            if latest is None:
                raise RuntimeError(f"the camera produced no frame within {self.warmup_s:.0f} s")
            detector = self.make_detector()
            detector.detect(latest[1], int(time.monotonic() * 1000))          # first inference is slow: pay for it before the clock starts
            self._build(detector)

    def _build(self, detector) -> None:
        monitor = GateMonitor(WorldState(), self.safety, self.arm, self.arm.path, timeout_s=self.timeout_s)
        camera = self.camera

        class AgeAwareSource:
            def frames(_self):
                for stamp, frame in camera.frames():
                    monitor.frame_age_ms = (time.monotonic() - stamp) * 1000
                    yield stamp, frame

        self.monitor = monitor
        self.loop = HandDetectionLoop(AgeAwareSource(), detector, self.calibration, monitor, hand_height=self.hand_height,
                                      target_hz=self.hz, table_to_arm=self.table_to_arm)
        self.loop.start()

    def reset(self) -> None:
        """Explicit operator action after a stop: a fresh monitor and detector, and no motion until a fresh observation arrives."""
        with self._lock:
            self._shutdown_loop()
            self.safety.reset()
            self.start()

    def _shutdown_loop(self) -> None:
        loop, self.loop = self.loop, None
        if loop is not None:
            self.monitor = None
            loop.close()

    def close(self) -> None:
        with self._lock:
            self._shutdown_loop()
            if self._camera_started:
                self.camera.close()
                self._camera_started = False

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
        return {"state": state, "scale": round(snap["speed_scale"], 3), "nearest_m": None if nearest is None else round(nearest, 3),
                "hands": len(hands), "fps": round(len(recent) / 2.0, 1),
                "latency_ms": {"p50": _percentile(decisions, 0.5), "p95": _percentile(decisions, 0.95)},
                "stale_s": None if age is None else round(age, 3), "reason": snap["reason"], "updated": now}


def live_hand_gate(arm, cam: int, model_path, *, marker_mm: float = 120.0, marker_id: int = 0, intrinsics=None, hfov_deg: float = 70.0,
                   hand_height: float = 0.08, width: int = 640, height: int = 360, safety: Safety | None = None, log=print) -> HandGate:
    """A real camera and MediaPipe, calibrated from the ArUco marker in view (keep hands out of the picture for a moment).

    Calibrates and runs at the same resolution (USB 2 cameras are only fast at low resolution). Needs the hands environment
    (mediapipe, opencv). Raises SystemExit with a plain message if the marker cannot be seen or the lens file does not match."""
    from pathlib import Path

    from .adapters.hands import MediaPipeHandDetector
    from .adapters.live_camera import LatestFrameCamera
    from .intrinsics import Intrinsics
    from .live_hands import CalibrationError, calibrate_from_frames, guess_intrinsics

    lens = Intrinsics.load(Path(intrinsics)) if intrinsics else None
    if lens is not None and lens.size != (width, height):
        raise SystemExit(f"{intrinsics} was calibrated at {lens.size[0]}x{lens.size[1]}; run the gate at that size (--gate-width/--gate-height)")
    k, dist = (lens.camera_matrix, lens.dist) if lens else (guess_intrinsics(width, height, hfov_deg), None)
    if lens is None:
        log("WARNING: lens not calibrated; hand positions are only roughly right (run scripts/calibrate_lens.py).")
    log("Calibrating the hand-gate camera from the marker (keep your hands out of view for a moment)...")
    probe = LatestFrameCamera(cam, f"cam{cam}", width, height, stall_s=6)
    frames = probe.frames()
    try:
        calibration = calibrate_from_frames(frames, marker_mm, k, dist, marker_id=marker_id, want=8, max_frames=60)
    except CalibrationError as exc:
        raise SystemExit(f"Hand-gate calibration failed: {exc}") from None
    finally:
        frames.close()
        probe.close()
    log(f"Calibrated: reprojection {calibration.reprojection_px:.2f} px at {width}x{height}")
    camera = LatestFrameCamera(cam, f"cam{cam}", width, height)
    return HandGate(camera, lambda: MediaPipeHandDetector(model_path), calibration, arm, safety=safety, hand_height=hand_height)
