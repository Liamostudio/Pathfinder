"""Continuous local speed gate and independent detector watchdog.

Inference/capture run on a separate thread. The watchdog never waits on either.
Use only with simulation until calibrated geometry and physical latency are validated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import threading
import time
from typing import Callable

from .adapters.base import HandDetector, SpeedControlledArm
from .adapters.hands import TableProjector, project_hands
from .safety import Safety, valid_position
from .world import Hand, Vec3, WorldState


@dataclass(frozen=True)
class Observation:
    captured_at: float  # monotonic seconds in this process, never replay/wall-clock time
    hands: tuple[Hand, ...]
    inference_ms: float


class HandSafetyMonitor:
    def __init__(self, world: WorldState, safety: Safety, arm: SpeedControlledArm,
                 path: Callable[[], tuple[Vec3, Vec3]], *, timeout_s=0.15,
                 watchdog_hz=100.0, clock=time.monotonic):
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 1:
            raise ValueError('Watchdog timeout must be in (0, 1] seconds')
        if not math.isfinite(watchdog_hz) or not 1 <= watchdog_hz <= 1000:
            raise ValueError('Invalid watchdog rate')
        self.world, self.safety, self.arm, self.path = world, safety, arm, path
        self.timeout_s, self.period, self.clock = timeout_s, 1 / watchdog_hz, clock
        self._lock = threading.Lock()
        self._gate_lock = threading.RLock()
        self._observation = None
        self._started = clock()
        self._shutdown = threading.Event()
        self._thread = None
        self._halt_sent = threading.Event()
        self.reason = 'waiting for a fresh observation'
        self.scale = 0.0
        self.arm.set_speed_scale(0.0)

    def publish(self, hands: list[Hand], captured_at: float, inference_ms: float = 0):
        now = self.clock()
        if not all(valid_position(h.position) and math.isfinite(h.radius_m) and h.radius_m >= 0 for h in hands):
            self.fail('invalid hand geometry')
            return
        if not math.isfinite(captured_at) or captured_at > now or not math.isfinite(inference_ms) or inference_ms < 0:
            self.fail('invalid observation timing')
            return
        observation = Observation(captured_at, tuple(replace(hand) for hand in hands), inference_ms)
        with self._lock:
            previous = self._observation
            if previous is not None and captured_at <= previous.captured_at:
                invalid = True
            else:
                self._observation = observation
                invalid = False
        if invalid:
            self.fail('non-increasing observation timestamps')
        else:
            self.tick()

    def fail(self, reason):
        # Set the latch BEFORE invoking the adapter; late results cannot resurrect motion.
        self.safety.stop()
        with self._gate_lock:
            self.reason = str(reason)
            self.scale = 0.0
            if not self._halt_sent.is_set():
                self._halt_sent.set()
                self.arm.halt()

    def tick(self):
        # Serialize read/evaluate/apply so an old clear result cannot overwrite a
        # newer near-hand result when inference and the watchdog finish together.
        with self._gate_lock:
            return self._evaluate()

    def _evaluate(self):
        if self._halt_sent.is_set() or self.safety.stopped:
            self.safety.stop()
            self.scale = 0.0
            if not self._halt_sent.is_set():
                self.fail('manual stop')
            return 0.0
        with self._lock:
            observation = self._observation
        now = self.clock()
        age = now - (observation.captured_at if observation else self._started)
        if age < 0 or age >= self.timeout_s:
            self.fail('detector timeout')
            return 0.0
        if observation is None:
            self.arm.set_speed_scale(0)
            return 0.0
        try:
            hands = [replace(hand) for hand in observation.hands]
            path = self.path()
            if len(path) != 2 or not all(valid_position(point) for point in path):
                raise ValueError('Invalid monitored path')
            scale = self.safety.allowed_speed(hands, path)
            if not math.isfinite(scale) or not 0 <= scale <= 1:
                raise ValueError('Invalid speed result')
            if self.clock() - observation.captured_at >= self.timeout_s:
                self.fail('detector timeout during evaluation')
                return 0.0
            self.world.update(hands=hands, arm_position=self.arm.position)
            self.arm.set_speed_scale(0.0 if self.safety.stopped else scale)
            self.scale = 0.0 if self.safety.stopped else scale
            self.reason = 'clear' if scale == 1 else 'hand nearby' if scale else 'hand in path'
            return self.scale
        except Exception as exc:
            self.fail(f'monitor error: {exc}')
            return 0.0

    def snapshot(self):
        with self._lock:
            observation = self._observation
        age = None if observation is None else self.clock() - observation.captured_at
        return {'speed_scale': self.scale, 'reason': self.reason, 'stopped': self.safety.stopped,
                'healthy': age is not None and 0 <= age < self.timeout_s and not self.safety.stopped,
                'age_ms': None if observation is None else (self.clock() - observation.captured_at) * 1000,
                'inference_ms': None if observation is None else observation.inference_ms}

    def start(self):
        if self._thread is not None:
            raise RuntimeError('Monitor already started')
        self._thread = threading.Thread(target=self._run, name='hand-watchdog', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._shutdown.is_set():
            self.tick()
            self._shutdown.wait(self.period)

    def close(self):
        self._shutdown.set()
        if not self.safety.stopped:
            self.fail('monitor closed')
        if self._thread is not None:
            self._thread.join(timeout=1)


class HandDetectionLoop:
    """Pace capture+inference at up to 30 Hz, independent of watchdog polling.

    Source timestamps must increase; they may be replay-relative or wall-clock seconds.
    Freshness uses monotonic receipt time, so neither domain is mixed into watchdog time.
    A source must deliver newly captured frames; repeated image content is not proof of a stall.
    """

    def __init__(self, source, detector: HandDetector, calibration: TableProjector,
                 monitor: HandSafetyMonitor, *, hand_height: float, target_hz=30.0,
                 uncertainty_m=0.03, table_to_arm=None):
        if not math.isfinite(target_hz) or not 1 <= target_hz <= 30:
            raise ValueError('Target rate must be in [1, 30] Hz')
        # Validate configuration even when the scene has no hands.
        project_hands([], calibration, hand_height=hand_height, uncertainty_m=uncertainty_m)
        self.source, self.detector, self.calibration, self.monitor = source, detector, calibration, monitor
        self.hand_height, self.period, self.uncertainty_m = hand_height, 1 / target_hz, uncertainty_m
        self.table_to_arm = table_to_arm
        self._shutdown = threading.Event()
        self._thread = None
        self.frames_processed = 0

    def start(self):
        if self._thread is not None:
            raise RuntimeError('Detection loop already started')
        self.monitor.start()
        self._thread = threading.Thread(target=self._run, name='hand-detection', daemon=True)
        self._thread.start()

    def _run(self):
        frames = None
        last_source_stamp = None
        timestamp_ms = -1
        try:
            frames = self.source.frames()
            for source_stamp, frame in frames:
                if self._shutdown.is_set() or self.monitor.safety.stopped:
                    break
                started = time.monotonic()
                if not math.isfinite(source_stamp) or (last_source_stamp is not None and source_stamp <= last_source_stamp):
                    raise ValueError('Frame timestamps did not advance')
                last_source_stamp = source_stamp
                timestamp_ms = max(timestamp_ms + 1, int(started * 1000))
                detections = self.detector.detect(frame, timestamp_ms)
                hands = project_hands(detections, self.calibration, hand_height=self.hand_height,
                                      uncertainty_m=self.uncertainty_m, table_to_arm=self.table_to_arm)
                self.monitor.publish(hands, started, (time.monotonic() - started) * 1000)
                self.frames_processed += 1
                if self._shutdown.wait(max(0, self.period - (time.monotonic() - started))):
                    break
            if not self._shutdown.is_set() and not self.monitor.safety.stopped:
                self.monitor.fail('frame source ended')
        except Exception as exc:
            self.monitor.fail(f'detector/source error: {exc}')
        finally:
            if hasattr(frames, 'close'):
                frames.close()
            self.detector.close()

    def close(self):
        self._shutdown.set()
        self.monitor.close()  # never wait for a blocked capture/inference before stopping
        if self._thread is not None:
            self._thread.join(timeout=1)


class MonitoredTracker:
    """Keep tool refreshes from replacing monitored hands with another tracker's empty list."""

    def __init__(self, objects_tracker, monitor: HandSafetyMonitor):
        self.objects_tracker, self.monitor = objects_tracker, monitor

    def objects(self):
        return self.objects_tracker.objects()

    def hands(self):
        self.monitor.tick()
        return [Hand(**hand) for hand in self.monitor.world.snapshot()['hands']]
