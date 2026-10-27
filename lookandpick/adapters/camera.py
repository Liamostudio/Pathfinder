"""Camera sources: live webcams and recorded footage behind one interface.

Develop against recordings first (ReplayCamera), then point the same code at LiveCamera.
Needs the `vision` extra; OpenCV is imported lazily.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Iterator, Protocol


def _cv2():
    import cv2  # noqa: PLC0415
    return cv2


class FrameSource(Protocol):
    name: str

    def frames(self) -> Iterator[tuple[float, object]]:
        """Yields (timestamp_s, BGR frame)."""
        ...


class LiveCamera:
    def __init__(self, index: int, name: str, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self.name, self._index = name, index
        self._size, self._fps = (width, height), fps

    def frames(self) -> Iterator[tuple[float, object]]:
        cv2 = _cv2()
        cap = cv2.VideoCapture(self._index)
        try:
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open camera {self._index}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._size[1])
            cap.set(cv2.CAP_PROP_FPS, self._fps)
            while True:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f"Camera {self._index} stopped delivering frames")
                yield time.time(), frame
        finally:
            cap.release()


class ReplayCamera:
    """Plays back a recording made by Recorder (a video file plus a .json of timestamps)."""

    def __init__(self, video: Path, name: str | None = None) -> None:
        self.video = Path(video)
        self.name = name or self.video.stem
        stamps = self.video.with_suffix(".json")
        self._stamps: list[float] = json.loads(stamps.read_text()) if stamps.exists() else []

    def frames(self) -> Iterator[tuple[float, object]]:
        cv2 = _cv2()
        cap = cv2.VideoCapture(str(self.video))
        i = 0
        try:
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video {self.video}")
            fps = cap.get(cv2.CAP_PROP_FPS)
            fps = fps if math.isfinite(fps) and fps > 0 else 30.0
            while True:
                ok, frame = cap.read()
                if not ok:
                    if self._stamps and i != len(self._stamps):
                        raise RuntimeError("Video frame count does not match timestamp sidecar")
                    return
                if self._stamps and i >= len(self._stamps):
                    raise RuntimeError("Video frame count does not match timestamp sidecar")
                yield (self._stamps[i] if i < len(self._stamps) else i / fps), frame
                i += 1
        finally:
            cap.release()


class Recorder:
    """Writes frames to <dir>/<name>.mp4 with per-frame timestamps in <name>.json."""

    def __init__(self, directory: Path, name: str, fps: float = 30.0) -> None:
        if not name or name in ('.', '..') or Path(name).name != name:
            raise ValueError("Recording name must be a single filename")
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Recording fps must be positive and finite")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{name}.mp4"
        if self.path.exists() or self.path.with_suffix('.json').exists():
            raise FileExistsError(f"Recording already exists: {self.path}")
        self._fps, self._writer, self._stamps = fps, None, []
        self._closed = False

    @property
    def frame_count(self) -> int:
        return len(self._stamps)

    def write(self, timestamp: float, frame) -> None:
        if self._closed:
            raise RuntimeError("Recorder is closed")
        if not math.isfinite(timestamp) or (self._stamps and timestamp < self._stamps[-1]):
            raise ValueError("Frame timestamps must be finite and nondecreasing")
        cv2 = _cv2()
        if self._writer is None:
            h, w = frame.shape[:2]
            self._writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self._fps, (w, h))
            if not self._writer.isOpened():
                self._writer.release()
                self._writer = None
                raise RuntimeError(f"Cannot create video {self.path}")
            self._size = (h, w)
        if frame.shape[:2] != self._size:
            raise ValueError("Frame size changed during recording")
        self._writer.write(frame)
        self._stamps.append(timestamp)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._writer is not None:
            self._writer.release()
        if self._stamps:
            self.path.with_suffix(".json").write_text(json.dumps(self._stamps))


def probe(max_index: int = 8) -> list[int]:
    """Indices that open and deliver a frame. Linux exposes metadata nodes that don't."""
    cv2 = _cv2()
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened() and cap.read()[0]:
            found.append(i)
        cap.release()
    return found
