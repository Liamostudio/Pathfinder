"""SamPeopleFeed: SAM 3.1 (Meta) segments people and hands in the live camera, and the voxel map shows where they could be.

    live frame -> SAM 3.1 text prompts "person", "hand" -> mask pixels -> rays through the calibration -> marker-frame points -> voxel observations

This is the Meta model in the live detection step. It is the SLOW, rich layer: SAM answers in about a second (0.6 to 1.0 s on this GPU), far too
slow for a 150 ms watchdog, so the fast MediaPipe hand gate stays the only safety path and this only paints the map. A single camera gives each
mask pixel a RAY, not a depth, so every sampled pixel is projected onto several heights above the table; that marks a line of cells along the ray,
conservatively (more red, never less). Nothing here can make a cube green.

Like VoxelFeed it is advisory and isolated: its own thread, read-only access to frames, and any failure (worker down, GPU busy with a reconstruction)
only means the map stops updating from SAM; the cubes expire to unknown and the safety gate is untouched.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .adapters.sam_client import SamClient, SamWorkerError

MAX_POINTS = 20000                      # the voxel service's limit for one observation
# (prompt, voxel kind, heights above the table to project each mask pixel onto, metres)
LAYERS = (("person", "person", (0.05, 0.3, 0.6, 1.0, 1.5)), ("hand", "hand", (0.02, 0.08, 0.15, 0.25)))


class SamPeopleFeed:
    def __init__(self, current: Callable[[], tuple | None], observe: Callable[[str, dict], object], frame_fn: Callable[[], object],
                 client: SamClient, calibration, *, layers=LAYERS, min_score: float = 0.4, points_per_mask: int = 150, hz: float = 1.0,
                 source: str = "sam-people", radius_m: float = 0.03, clock=time.time) -> None:
        if not 0 < hz <= 5:
            raise ValueError("hz must be in (0, 5]: SAM is a slow layer")
        self.current, self.observe, self.frame_fn, self.client, self.calibration = current, observe, frame_fn, client, calibration
        self.layers, self.min_score, self.points_per_mask, self.period = layers, min_score, points_per_mask, 1 / hz
        self.source, self.radius_m, self._clock = source, radius_m, clock
        self._sequence, self._stop, self._thread = 0, threading.Event(), None
        self.sent = self.errors = 0
        self.last_error: str | None = None
        self.last_ok: float | None = None
        self.last_found: dict[str, int] = {}

    def _project(self, pixels, heights) -> list[list[float]]:
        points = []
        for x, y in pixels:
            for z in heights:
                try:
                    p = self.calibration.pixel_to_table((x, y), z=z)
                except Exception:                           # a ray that never meets that plane
                    continue
                if all(abs(float(c)) < 5.0 for c in p):
                    points.append([float(p[0]), float(p[1]), z])
        return points

    def tick(self) -> int:
        """One look: segment each layer's prompt, project, offer. Returns observations accepted; never raises."""
        try:
            target, frame = self.current(), self.frame_fn()
            if target is None or frame is None:
                return 0
            import cv2
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                raise SamWorkerError("cannot encode the frame")
            key, revision, calibration = target
            accepted = 0
            for prompt, kind, heights in self.layers:
                detections = [d for d in self.client.segment(jpeg.tobytes(), prompt, 4, self.points_per_mask) if d.score >= self.min_score]
                self.last_found[prompt] = len(detections)
                pixels = [p for d in detections for p in (d.points or (d.centroid,))]
                points = self._project(pixels, heights)[:MAX_POINTS]
                if not points:
                    continue
                payload = {"mode": "points", "kind": kind, "frame": "marker_metres", "points": points, "radius": self.radius_m,
                           "revision": revision, "sequence": self._sequence, "source": f"{self.source}-{kind}",
                           "captured_at_ms": int(self._clock() * 1000), "calibration_id": calibration}
                self._sequence += 1
                self.observe(key, payload)
                accepted += 1
            self.sent += accepted
            self.last_ok = self._clock()
            return accepted
        except Exception as exc:                            # an advisory layer must never raise into anything
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return 0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the SAM people feed is already running")
        self._thread = threading.Thread(target=self._run, name="sam-people", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.tick()
            self._stop.wait(max(0.05, self.period - (time.monotonic() - started)))

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def status(self) -> dict:
        return {"running": self._thread is not None and self._thread.is_alive() and not self._stop.is_set(), "source": self.source, "sent": self.sent,
                "errors": self.errors, "last_error": self.last_error, "last_ok": self.last_ok, "last_found": dict(self.last_found)}
