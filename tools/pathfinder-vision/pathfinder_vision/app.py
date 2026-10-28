"""Pathfinder Vision: a camera viewer that states exactly what is running and what it is detecting."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .models import MODELS, default_models_dir, download, model_path


def build_factories(models_dir: Path):
    from .detectors import HandDetector, PersonBoxDetector, PersonSkeletonDetector  # noqa: PLC0415
    return {"hands": lambda: HandDetector(model_path("hands", models_dir)),
            "person-box": lambda: PersonBoxDetector(model_path("person-box", models_dir)),
            "person-skeleton": lambda: PersonSkeletonDetector(model_path("person-skeleton", models_dir))}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="pathfinder-vision", description=__doc__)
    p.add_argument("--camera", type=int, default=0, help="camera number (/dev/videoN)")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--people", choices=("off", "boxes", "skeletons"), default="off", help="start with people detection on")
    p.add_argument("--no-hands", action="store_true", help="start with hand detection off")
    p.add_argument("--people-every", type=int, default=2, help="run the (slower) people detector every N frames")
    p.add_argument("--models-dir", type=Path, default=default_models_dir())
    p.add_argument("--out-dir", type=Path, default=Path.home() / "Videos" / "pathfinder-vision")
    p.add_argument("--view-width", type=int, default=1024)
    p.add_argument("--list-cameras", action="store_true", help="list cameras and their numbers, then exit")
    p.add_argument("--download-models", action="store_true", help="download the three model files, then exit")
    p.add_argument("--no-window", action="store_true", help="headless self-test: run --seconds and print a summary")
    p.add_argument("--seconds", type=float, default=10.0, help="with --no-window")
    p.add_argument("--version", action="version", version=f"pathfinder-vision {__version__}")
    a = p.parse_args(argv)

    if a.list_cameras:
        base = Path("/sys/class/video4linux")
        found = False
        for node in sorted(base.glob("video*"), key=lambda q: int(q.name[5:])) if base.is_dir() else []:
            try:
                if (node / "index").read_text().strip() == "0":  # first node of a device; the rest are metadata
                    print(f"--camera {node.name[5:]}   {(node / 'name').read_text().strip()}")
                    found = True
            except OSError:
                pass
        if not found:
            print("No cameras found.")
        return

    if a.download_models:
        print(f"Downloading to {a.models_dir} (about 15 MB in total):")
        for m in MODELS.values():
            print(f"  {m.filename}: {m.description}")
        download(MODELS.keys(), a.models_dir)
        return

    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # OpenCV's window under Wayland needs XWayland
    from .camera import Camera
    from .pipeline import Pipeline
    from .recorder import Recorder
    from . import ui
    import cv2

    camera = Camera(a.camera, a.width, a.height)
    print(f"Opening camera {a.camera} at {a.width}x{a.height} ...", flush=True)
    camera.start()
    deadline = time.monotonic() + 15
    while camera.latest() is None and time.monotonic() < deadline and camera.error is None:
        time.sleep(0.05)
    if camera.latest() is None:
        camera.close()
        raise SystemExit(camera.error or "No frame from the camera within 15 seconds.")
    pipeline = Pipeline(camera, build_factories(a.models_dir), a.people_every)
    pipeline.hands_on = not a.no_hands
    pipeline.people_mode = {"off": "off", "boxes": "person-box", "skeletons": "person-skeleton"}[a.people]
    pipeline.start()
    recorder = Recorder(a.out_dir)
    frames = camera.latest()[2].shape
    cam_info = {"name": camera.name, "width": frames[1], "height": frames[0]}

    def current():
        snap = pipeline.snapshot()
        if snap is None:
            return None
        frame, detections, rows, fps, pipeline_ms = snap
        state = {"camera": cam_info, "fps": fps, "pipeline_ms": pipeline_ms, "detectors": rows, "detections": detections,
                 "recording": recorder.elapsed() if recorder.active else None}
        return ui.render(cv2, frame, detections, state, a.view_width), state

    try:
        if a.no_window:
            t0 = time.monotonic()
            counts = {"hands": 0, "person": 0}
            samples = 0
            while time.monotonic() - t0 < a.seconds:
                got = current()
                if got:
                    samples += 1
                    for d in got[1]["detections"]:
                        counts[d.kind] += 1
                time.sleep(0.05)
            got = current()
            if got:
                for row in got[1]["detectors"]:
                    print(f"{row['title']}: {row['status']}  {row['ms']:.1f} ms  {row['count']} found"
                          + (f"  [{row['note']}]" if row["note"] else ""))
                print(f"fps {got[1]['fps']:.1f}, pipeline {got[1]['pipeline_ms']:.0f} ms/frame")
            return
        cv2.namedWindow("Pathfinder Vision", cv2.WINDOW_AUTOSIZE)
        while True:
            got = current()
            if got:
                image, _ = got
                cv2.imshow("Pathfinder Vision", image)
                recorder.add(image)
            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty("Pathfinder Vision", cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("h"):
                pipeline.toggle_hands()
            elif key == ord("p"):
                pipeline.cycle_people()
            elif key == ord("r") and got:
                if recorder.active:
                    print(f"Saved {recorder.stop()}")
                else:
                    print(f"Recording to {recorder.start((got[0].shape[1], got[0].shape[0]))}")
            elif key == ord("s") and got:
                a.out_dir.mkdir(parents=True, exist_ok=True)
                shot = a.out_dir / f"pathfinder-vision-{time.strftime('%Y%m%d-%H%M%S')}.png"
                cv2.imwrite(str(shot), got[0])
                print(f"Saved {shot}")
    finally:
        if recorder.active:
            print(f"Saved {recorder.stop()}")
        pipeline.close()
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
