"""A webcam source that always hands out the freshest frame.

`cv2.VideoCapture.read()` queues frames, so a consumer slower than the camera sees stale images and
the hand gate would react to the past. A background thread keeps only the newest frame; `frames()`
yields each new frame once, and raises if none arrives within `stall_s` so the watchdog fails safe.
Needs the `vision` extra; OpenCV is imported lazily.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterator


def _open_v4l2(index: int, width: int, height: int, fps: int):
    import cv2  # noqa: PLC0415
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class LatestFrameCamera:
    def __init__(self, index: int, name: str = "camera", width: int = 1280, height: int = 720, fps: int = 30,
                 stall_s: float = 0.5, first_frame_s: float = 8.0, open_capture: Callable | None = None) -> None:
        self.name, self.stall_s, self.first_frame_s = name, stall_s, first_frame_s
        self._open = open_capture or (lambda: _open_v4l2(index, width, height, fps))
        self._cond = threading.Condition()
        self._frame, self._stamp, self._seq, self._error = None, 0.0, 0, None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames_grabbed = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        cap = self._open()
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Cannot open camera {self.name}")
        self._thread = threading.Thread(target=self._grab, args=(cap,), name=f"grab-{self.name}", daemon=True)
        self._thread.start()

    def _grab(self, cap) -> None:
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                with self._cond:
                    if not ok or frame is None:
                        self._error = f"camera {self.name} stopped delivering frames"
                        self._cond.notify_all()
                        return
                    self._frame, self._stamp, self._seq = frame, time.monotonic(), self._seq + 1
                    self.frames_grabbed += 1
                    self._cond.notify_all()
        finally:
            cap.release()

    def latest(self):
        """(monotonic capture time, frame, age in seconds) of the newest frame, or None. For display."""
        with self._cond:
            if self._frame is None:
                return None
            return self._stamp, self._frame, time.monotonic() - self._stamp

    def frames(self) -> Iterator[tuple[float, object]]:
        self.start()
        seen = 0
        while not self._stop.is_set():
            # Webcams can take seconds to deliver the first frame after being plugged in; only then enforce stall_s.
            wait = self.first_frame_s if seen == 0 else self.stall_s
            with self._cond:
                if not self._cond.wait_for(lambda: self._seq != seen or self._error or self._stop.is_set(), wait):
                    raise RuntimeError(f"camera {self.name} stalled (no frame for {wait:.2f} s)")
                if self._error:
                    raise RuntimeError(self._error)
                if self._stop.is_set():
                    return
                seen, stamp, frame = self._seq, self._stamp, self._frame
            yield stamp, frame.copy()

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)
