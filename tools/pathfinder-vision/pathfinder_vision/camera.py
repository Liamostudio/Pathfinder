"""A webcam that always provides the newest frame (a background thread drops the backlog)."""
from __future__ import annotations

import threading
import time


class Camera:
    def __init__(self, index: int, width: int, height: int, fps: int = 30, open_capture=None):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self._open = open_capture
        self._lock = threading.Lock()
        self._frame, self._stamp, self._seq, self.error = None, 0.0, 0, None
        self._stop = threading.Event()
        self._thread = None
        self.name = f"camera {index}"

    def _default_open(self):
        import cv2  # noqa: PLC0415
        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def start(self) -> None:
        cap = (self._open or self._default_open)()
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Cannot open camera {self.index}")
        self.name = _device_name(self.index)
        self._thread = threading.Thread(target=self._grab, args=(cap,), daemon=True)
        self._thread.start()

    def _grab(self, cap) -> None:
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.error = "camera stopped delivering frames"
                    return
                with self._lock:
                    self._frame, self._stamp, self._seq = frame, time.monotonic(), self._seq + 1
        finally:
            cap.release()

    def latest(self):
        """(sequence number, capture time, frame) of the newest frame, or None before the first one."""
        with self._lock:
            return None if self._frame is None else (self._seq, self._stamp, self._frame)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def list_cameras(base: str = "/sys/class/video4linux") -> list[tuple[int, str]]:
    """(number, name) of each real camera. A device exposes several /dev/video nodes; only the first captures."""
    from pathlib import Path  # noqa: PLC0415
    root, found = Path(base), []
    if root.is_dir():
        for node in sorted(root.glob("video*"), key=lambda q: int(q.name[5:])):
            try:
                if (node / "index").read_text().strip() == "0":
                    found.append((int(node.name[5:]), (node / "name").read_text().strip()))
            except (OSError, ValueError):
                pass
    return found


def in_use(index: int, proc: str = "/proc") -> bool:
    """True if another process of this user has /dev/videoN open (a second viewer could not stream from it)."""
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    target, me = f"/dev/video{index}", str(os.getpid())
    for fd_dir in Path(proc).glob("[0-9]*/fd"):
        if fd_dir.parent.name == me:
            continue
        try:
            for fd in fd_dir.iterdir():
                if os.readlink(fd) == target:
                    return True
        except OSError:
            continue
    return False


def first_free_camera(cameras=None) -> int | None:
    """The first listed camera nobody else is using; the first camera if all are busy; None if there are none."""
    cameras = list_cameras() if cameras is None else cameras
    for number, _ in cameras:
        if not in_use(number):
            return number
    return cameras[0][0] if cameras else None


def _device_name(index: int) -> str:
    try:
        with open(f"/sys/class/video4linux/video{index}/name") as f:
            return f.read().strip()
    except OSError:
        return f"camera {index}"
