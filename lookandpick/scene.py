"""SamScene: look at the table when it matters. Spark names what is there (only when the picture changed), SAM 3.1 finds it.

    frame -> [changed since the last labelling?] -> Muse Spark names the objects -> SAM 3.1 segments each name -> table positions

The expensive step (a Spark call, which costs credits) runs on scene CHANGE, never on a timer; the cheap local SAM step runs on every
refresh, which the runtime triggers when a command arrives. Both are written to the call log by name, in Deck's timeline shape, with
input, output and latency. If the table cannot be seen (no frame, worker down, Spark unreachable with nothing cached) `refresh` raises
`SceneUnavailable` and the runtime refuses to move: it never acts on a scene it could not look at.
"""
from __future__ import annotations

import time

from .adapters.sam_client import SamWorkerError
from .models.base import ModelError
from .models.calllog import CallLog
from .scene_labeller import SceneLabelError, SceneLabeller

CHANGE_THRESHOLD = 6.0          # mean absolute grey-level difference (0-255) between small versions of two frames


class SceneUnavailable(RuntimeError):
    """The table could not be looked at, so the scene is unknown."""


def frame_changed(previous, current, threshold: float = CHANGE_THRESHOLD) -> bool:
    """True if two frames differ enough to be worth a new labelling. Compares 64x36 grey versions; None means 'never labelled'."""
    import cv2
    import numpy as np
    if previous is None:
        return True
    small = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32) for f in (previous, current)]
    return float(np.abs(small[0] - small[1]).mean()) > threshold


class SamScene:
    def __init__(self, frame_fn, labeller: SceneLabeller, tracker, log: CallLog | None = None, *, change_threshold: float = CHANGE_THRESHOLD,
                 fixed_prompts: list[str] | None = None, clock=time.monotonic):
        """`frame_fn()` returns the newest BGR frame (or None). `fixed_prompts` skips Spark entirely (offline, or to save credits)."""
        self.frame_fn, self.labeller, self.tracker, self.log = frame_fn, labeller, tracker, log
        self.threshold, self.fixed, self._clock = change_threshold, list(fixed_prompts or []), clock
        self.labels: list[str] = list(self.fixed)
        self._labelled_frame = None
        self.labelled = 0                               # how many Spark labelling calls were made (a cost counter)

    def refresh(self) -> list:
        frame = self.frame_fn()
        if frame is None:
            raise SceneUnavailable("no camera frame")
        if not self.fixed and frame_changed(self._labelled_frame, frame, self.threshold):
            self._relabel(frame)
        if not self.labels:
            self.tracker.refresh(frame, [])                # nothing to look for: the scene is genuinely empty
            return self.tracker.objects()
        started = time.perf_counter()
        try:
            objects = self.tracker.refresh(frame, self.labels)
        except SamWorkerError as exc:
            self._record("SAM 3.1", ", ".join(self.labels), str(exc), started, "error")
            raise SceneUnavailable(f"SAM worker: {exc}") from None
        found = {label: sum(1 for o in objects if o.label == label) for label in self.labels}
        self._record("SAM 3.1", ", ".join(self.labels), "; ".join(f"{k}: {v} found" for k, v in found.items()) or "nothing to find", started)
        return objects

    def _relabel(self, frame) -> None:
        started = time.perf_counter()
        try:
            self.labels = self.labeller.label(frame)     # logged by the model client itself (name, input, output, cost)
        except (ModelError, SceneLabelError) as exc:
            if self.log is not None:
                self.log.record("Scene labeller", "label the table", str(exc), (time.perf_counter() - started) * 1000, status="error")
            if not self.labels:
                raise SceneUnavailable(f"could not name the objects: {exc}") from None
            return                                       # keep the previous names rather than going blind; the picture is just not re-labelled
        self._labelled_frame, self.labelled = frame, self.labelled + 1

    def _record(self, source: str, text: str, output: str, started: float, status: str = "ok") -> None:
        if self.log is not None:
            self.log.record(source, text, output, (time.perf_counter() - started) * 1000, status=status)
