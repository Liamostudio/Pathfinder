"""Pathfinder Vision: a camera viewer that states exactly what is running and what it is detecting."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .camera import Camera, first_free_camera, in_use, list_cameras
from .models import MODELS, default_models_dir, download, model_path


def build_factories(models_dir: Path):
    from .detectors import HandDetector, PersonBoxDetector, PersonSkeletonDetector  # noqa: PLC0415
    return {"hands": lambda: HandDetector(model_path("hands", models_dir)),
            "person-box": lambda: PersonBoxDetector(model_path("person-box", models_dir)),
            "person-skeleton": lambda: PersonSkeletonDetector(model_path("person-skeleton", models_dir))}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="pathfinder-vision", description=__doc__)
    p.add_argument("--camera", type=int, default=None,
                   help="camera number (/dev/videoN); default: the first camera nobody else is using")
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
        cameras = list_cameras()
        for number, name in cameras:
            print(f"--camera {number}   {name}" + ("   (in use by another program)" if in_use(number) else ""))
        if not cameras:
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
    from .pipeline import Pipeline
    from .recorder import Recorder
    from . import ui
    import cv2

    if a.camera is None:
        a.camera = first_free_camera()
        if a.camera is None:
            raise SystemExit("No cameras found. Plug one in, then run: pathfinder-vision --list-cameras")
        print(f"Using camera {a.camera} (the first one not in use).", flush=True)
    camera = Camera(a.camera, a.width, a.height)
    print(f"Opening camera {a.camera} at {a.width}x{a.height} ...", flush=True)
    camera.start()
    deadline = time.monotonic() + 15
    while camera.latest() is None and time.monotonic() < deadline and camera.error is None:
        time.sleep(0.05)
    if camera.latest() is None:
        camera.close()
        busy = " Another program (perhaps another Pathfinder Vision window) is already using it." if in_use(a.camera) else ""
        raise SystemExit(f"Camera {a.camera}: {camera.error or 'no frame within 15 seconds'}.{busy} "
                         "Try another with --camera N (see --list-cameras).")
    window = "Pathfinder Vision"  # the panel names the camera, which can change from the dropdown
    pipeline = Pipeline(camera, build_factories(a.models_dir), a.people_every)
    pipeline.hands_on = not a.no_hands
    pipeline.people_mode = {"off": "off", "boxes": "person-box", "skeletons": "person-skeleton"}[a.people]
    pipeline.start()
    recorder = Recorder(a.out_dir, tag=f"cam{a.camera}")
    def choices():
        return [(n, name + (" (in use)" if in_use(n) else "")) for n, name in list_cameras()]

    dd = {"open": False, "items": choices(), "selected": a.camera, "status": ""}
    holder = {"camera": camera}

    def switch_camera(number: int) -> None:
        """Open another camera in the background (the UI stays live), then hand it to the pipeline."""
        if number == dd["selected"]:
            return
        if in_use(number):
            dd["status"] = f"camera {number} is in use by another program"
            return
        dd["status"] = f"opening camera {number} ..."

        def work():
            new = Camera(number, a.width, a.height)
            try:
                new.start()
                end = time.monotonic() + 15
                while new.latest() is None and time.monotonic() < end and new.error is None:
                    time.sleep(0.05)
                if new.latest() is None:
                    raise RuntimeError(new.error or "no frame within 15 s")
            except Exception as e:  # noqa: BLE001
                new.close()
                dd["status"] = f"camera {number}: {e}"
                return
            if recorder.active:
                print(f"Saved {recorder.stop()} (camera changed)")
            pipeline.set_camera(new)
            holder["camera"] = new
            dd["selected"], dd["status"] = number, ""

        threading.Thread(target=work, daemon=True).start()

    def on_mouse(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        geo = ui.dropdown_geometry(a.view_width, 480, len(dd["items"]))
        if dd["open"]:
            for (number, _), rect in zip(dd["items"], geo["items"]):
                if ui.inside(rect, x, y):
                    switch_camera(number)
            dd["open"] = False
        elif ui.inside(geo["box"], x, y):
            dd["items"] = choices()  # refresh, so a camera plugged in a moment ago appears
            dd["open"] = True

    def current():
        snap = pipeline.snapshot()
        if snap is None:
            return None
        frame, detections, rows, fps, pipeline_ms = snap
        cam_info = {"name": holder["camera"].name, "width": frame.shape[1], "height": frame.shape[0]}
        state = {"camera": cam_info, "dropdown": dd, "fps": fps, "pipeline_ms": pipeline_ms, "detectors": rows, "detections": detections,
                 "recording": recorder.elapsed() if recorder.active else None}
        return ui.render(cv2, frame, detections, state, a.view_width), state

    try:
        if a.no_window:
            t0 = time.monotonic()
            counts = {"hand": 0, "person": 0}
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
        cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(window, on_mouse)
        while True:
            got = current()
            if got:
                image, _ = got
                cv2.imshow(window, image)
                recorder.add(image)
            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("h"):
                pipeline.toggle_hands()
            elif key == ord("p"):
                pipeline.cycle_people()
            elif key == ord("c"):
                numbers = [n for n, _ in list_cameras()]
                if numbers and dd["selected"] in numbers:
                    switch_camera(numbers[(numbers.index(dd["selected"]) + 1) % len(numbers)])
            elif key == ord("r") and got:
                if recorder.active:
                    print(f"Saved {recorder.stop()}")
                else:
                    print(f"Recording to {recorder.start((got[0].shape[1], got[0].shape[0]))}")
            elif key == ord("s") and got:
                a.out_dir.mkdir(parents=True, exist_ok=True)
                shot = a.out_dir / f"pathfinder-vision-cam{a.camera}-{time.strftime('%Y%m%d-%H%M%S')}.png"
                cv2.imwrite(str(shot), got[0])
                print(f"Saved {shot}")
    finally:
        if recorder.active:
            print(f"Saved {recorder.stop()}")
        pipeline.close()
        holder["camera"].close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
