"""SamTracker: text-prompted SAM 3.1 detections turned into table positions through the ArUco calibration.

Implements the `Tracker` protocol (`objects()`, `hands()`). It is refreshed on events, never on a timer: call `refresh(frame, prompts)`
when the scene may have changed (a new command, the arm finished a move). Each prompt is a short noun phrase ("red cube"); each
detection's mask centre is intersected with the plane at half the object's height, giving a position in the marker (world) frame,
the same frame as the simulator, the calibration and the arm.

It never invents objects: if the worker fails, `refresh` raises and the previous objects are kept untouched with their age.
"""
from __future__ import annotations

import re
import time

from ..world import Hand, TrackedObject
from .sam_client import SamClient, SamWorkerError  # noqa: F401  (SamWorkerError re-exported for callers)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "object"


class SamTracker:
    def __init__(self, client: SamClient, calibration, calibration_size: tuple[int, int], *, object_height_m: float = 0.03,
                 min_score: float = 0.35, match_radius_m: float = 0.04, max_per_prompt: int = 3,
                 workspace: tuple[tuple[float, float], tuple[float, float]] = ((-0.35, 0.35), (-0.35, 0.35)), clock=time.monotonic):
        self.client, self.calibration, self.size = client, calibration, calibration_size
        self.object_height_m, self.min_score, self.match_radius_m, self.max_per_prompt = object_height_m, min_score, match_radius_m, max_per_prompt
        self.workspace = workspace
        self._objects: list[TrackedObject] = []
        self._counts: dict[str, int] = {}
        self._clock, self._refreshed_at = clock, None
        self.last_detections: dict[str, list] = {}

    # -- Tracker protocol --------------------------------------------------------------------------------------------
    def objects(self) -> list[TrackedObject]:
        return list(self._objects)

    def hands(self) -> list[Hand]:
        return []                                  # hands belong to the fast hand loop, never to SAM

    # -- refresh -----------------------------------------------------------------------------------------------------
    def age_s(self) -> float | None:
        return None if self._refreshed_at is None else self._clock() - self._refreshed_at

    def refresh(self, frame, prompts: list[str]) -> list[TrackedObject]:
        """Segment each prompt in `frame` (a BGR image at the calibration's resolution scale) and rebuild the object list."""
        import cv2
        height, width = frame.shape[:2]
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise SamWorkerError("cannot encode the frame")
        sx, sy = self.size[0] / width, self.size[1] / height          # frame pixels -> calibration pixels
        found: list[tuple[str, tuple[float, float, float], float]] = []
        detections: dict[str, list] = {}
        for prompt in prompts:
            wanted = self.client.segment(jpeg.tobytes(), prompt, self.max_per_prompt)
            detections[prompt] = wanted
            for d in wanted:
                if d.score < self.min_score:
                    continue
                position = self._to_table(d.centroid[0] * sx, d.centroid[1] * sy)
                if position is not None:
                    found.append((prompt, position, d.score))
        self.last_detections = detections
        self._objects = self._associate(found)
        self._refreshed_at = self._clock()
        return self.objects()

    def _to_table(self, u: float, v: float):
        try:
            point = self.calibration.pixel_to_table((u, v), z=self.object_height_m / 2)
        except Exception:                                              # a ray that misses the plane, a degenerate pixel
            return None
        x, y = float(point[0]), float(point[1])
        (x0, x1), (y0, y1) = self.workspace
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return None                                                # not on the table we care about
        return (x, y, self.object_height_m / 2)

    def _associate(self, found):
        """Keep an object's id when something with the same label is within `match_radius_m` of where it was; else a new id."""
        previous, taken, out = list(self._objects), set(), []
        for prompt, position, _score in sorted(found, key=lambda f: -f[2]):
            best = None
            for old in previous:
                if old.id in taken or old.label != prompt:
                    continue
                gap = ((old.position[0] - position[0]) ** 2 + (old.position[1] - position[1]) ** 2) ** 0.5
                if gap <= self.match_radius_m and (best is None or gap < best[0]):
                    best = (gap, old)
            if best:
                taken.add(best[1].id)
                out.append(TrackedObject(best[1].id, prompt, position))
            else:
                slug = _slug(prompt)
                self._counts[slug] = self._counts.get(slug, 0) + 1
                out.append(TrackedObject(f"{slug}-{self._counts[slug]}", prompt, position))
        return sorted(out, key=lambda o: o.id)
