"""Records the whole window (view plus panel) to MP4 in real time, so the footage shows what was running."""
from __future__ import annotations

import time
from pathlib import Path


class Recorder:
    def __init__(self, out_dir: Path, fps: float = 30.0, clock=time.monotonic) -> None:
        self.out_dir, self.fps, self.clock = Path(out_dir), fps, clock
        self._writer = None
        self._start = 0.0
        self.written = 0
        self.path: Path | None = None

    @property
    def active(self) -> bool:
        return self._writer is not None

    def elapsed(self) -> str:
        s = int(self.clock() - self._start) if self.active else 0
        return f"{s // 60:02d}:{s % 60:02d}"

    def start(self, size: tuple[int, int]) -> Path:
        import cv2  # noqa: PLC0415
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.out_dir / f"pathfinder-vision-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, size)
        if not writer.isOpened():
            raise RuntimeError(f"cannot write {self.path}")
        self._writer, self._start, self.written = writer, self.clock(), 0
        return self.path

    def add(self, image) -> None:
        """Write the image enough times to keep the video in real time even if frames arrive unevenly."""
        if not self.active:
            return
        due = int((self.clock() - self._start) * self.fps) + 1 - self.written
        for _ in range(max(1, min(due, int(self.fps)))):  # never more than a second of catch-up
            self._writer.write(image)
            self.written += 1

    def stop(self) -> Path | None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        return self.path
