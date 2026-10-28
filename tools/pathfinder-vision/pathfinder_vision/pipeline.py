"""Runs the enabled detectors on the newest camera frame, off the UI thread, and reports what they did."""
from __future__ import annotations

import threading
import time
from typing import Callable

PEOPLE_MODES = ("off", "person-box", "person-skeleton")
ROW_INFO = {  # what the panel says about each detector
    "hands": ("hand", "H", "HANDS", "MediaPipe Hand Landmarker (21 points per hand)"),
    "person-box": ("person", "P", "PEOPLE: BOXES", "MediaPipe EfficientDet-Lite0, 'person' class only"),
    "person-skeleton": ("person", "P", "PEOPLE: SKELETONS", "MediaPipe Pose Landmarker lite (33 body points)"),
}


class Pipeline(threading.Thread):
    def __init__(self, camera, factories: dict[str, Callable], people_every: int = 2, clock=time.monotonic) -> None:
        super().__init__(name="pipeline", daemon=True)
        self.camera, self.factories, self.people_every, self.clock = camera, factories, max(1, people_every), clock
        self.hands_on, self.people_mode = True, "off"
        self._detectors, self._errors, self._last, self._ms, self._count = {}, {}, {}, {}, {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._published = None
        self._times: list[float] = []
        self._pipeline_ms = 0.0

    # --- controls (UI thread) ---
    def toggle_hands(self) -> None:
        self.hands_on = not self.hands_on

    def cycle_people(self) -> None:
        self.people_mode = PEOPLE_MODES[(PEOPLE_MODES.index(self.people_mode) + 1) % len(PEOPLE_MODES)]

    def close(self) -> None:
        self._stop.set()
        self.join(timeout=3)
        for d in self._detectors.values():
            d.close()

    # --- worker ---
    def _get(self, key: str):
        if key in self._detectors:
            return self._detectors[key]
        if key in self._errors:
            return None
        try:
            self._detectors[key] = self.factories[key]()
        except FileNotFoundError as e:
            self._errors[key] = ("missing", f"model missing: run pathfinder-vision --download-models ({e})")
        except Exception as e:  # noqa: BLE001  - shown in the panel rather than crashing the viewer
            self._errors[key] = ("error", f"{type(e).__name__}: {e}")
        return self._detectors.get(key)

    def run(self) -> None:
        last_seq, n = 0, 0
        while not self._stop.is_set():
            item = self.camera.latest()
            if item is None or item[0] == last_seq:
                time.sleep(0.002)
                continue
            last_seq, stamp, frame = item
            started = self.clock()
            active = (["hands"] if self.hands_on else []) + ([self.people_mode] if self.people_mode != "off" else [])
            detections = []
            for key in active:
                det = self._get(key)
                if det is None:
                    continue
                every = self.people_every if key != "hands" else 1
                if n % every == 0 or key not in self._last:
                    t0 = self.clock()
                    try:
                        self._last[key] = det.detect(frame, int(self.clock() * 1000))
                    except Exception as e:  # noqa: BLE001
                        self._errors[key] = ("error", f"{type(e).__name__}: {e}")
                        self._detectors.pop(key, None)
                        self._last.pop(key, None)
                        continue
                    ms = (self.clock() - t0) * 1000
                    self._ms[key] = ms if key not in self._ms else 0.8 * self._ms[key] + 0.2 * ms
                detections += self._last.get(key, [])
                self._count[key] = len(self._last.get(key, []))
            n += 1
            now = self.clock()
            self._times = [t for t in self._times if now - t < 2.0] + [now]
            self._pipeline_ms = 0.8 * self._pipeline_ms + 0.2 * (now - started) * 1000
            with self._lock:
                self._published = (frame, detections)

    # --- reading (UI thread) ---
    def snapshot(self):
        with self._lock:
            published = self._published
        if published is None:
            return None
        frame, detections = published
        rows = []
        for key, enabled in (("hands", self.hands_on), ("person", self.people_mode != "off")):
            k = "hands" if key == "hands" else (self.people_mode if enabled else "person-box")
            kind, hotkey, title, what = ROW_INFO[k]
            if not enabled:
                status, note = "off", None
                if key == "person":
                    title, what = "PEOPLE", "off (press P for boxes, again for skeletons)"
            elif k in self._errors:
                status, note = self._errors[k]
            else:
                status, note = "on", None
            rows.append({"kind": kind, "hotkey": hotkey, "title": title, "what": what, "status": status, "note": note,
                         "backend": "CPU", "ms": self._ms.get(k, 0.0), "count": self._count.get(k, 0),
                         "every": self.people_every if k != "hands" else 1})
        span = (self._times[-1] - self._times[0]) if len(self._times) > 1 else 0
        fps = (len(self._times) - 1) / span if span > 0 else 0.0
        return frame, detections, rows, fps, self._pipeline_ms
