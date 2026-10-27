"""Check table calibration on a real camera (or a saved image) using the printed ArUco marker.

    python scripts/check_calibration.py --cam 0 --marker-mm 120
    python scripts/check_calibration.py --image shot.png --marker-mm 120 --no-window

Shows the marker pose axes, the reprojection error and a 10 cm grid on the table. Click anywhere
to read off its table position in cm (origin = marker centre, x right, y up the page, z up); hold
a ruler on the table to see whether the readout is within 1 cm. Press s to save a frame, q to quit.

Lens intrinsics are not calibrated yet: they are estimated from --hfov (degrees), so expect errors
above the synthetic 1-5 mm until a checkerboard calibration is added. Needs the `vision` extra.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from lookandpick.calibration import TableCalibration, detect_marker


def guess_intrinsics(width: int, height: int, hfov_deg: float) -> np.ndarray:
    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1.0]])


def annotate(frame, cal: TableCalibration | None, corners, click_text: str):
    import cv2  # noqa: PLC0415
    out = frame.copy()
    if corners is not None:
        cv2.polylines(out, [corners.astype(np.int32)], True, (0, 255, 0), 2)
    if cal is not None:
        for i in range(-3, 4):  # 10 cm grid, 30 cm either side of the marker
            for a, b in (((i * .1, -.3), (i * .1, .3)), ((-.3, i * .1), (.3, i * .1))):
                p, q = cal.table_to_pixel(a), cal.table_to_pixel(b)
                cv2.line(out, tuple(int(v) for v in p), tuple(int(v) for v in q), (255, 160, 0), 1)
        cv2.drawFrameAxes(out, cal.camera_matrix, cal.dist, cal.rvec, cal.tvec, 0.06, 3)
        cam = cal.camera_position()
        status = f"reproj {cal.reprojection_px:.2f}px  camera x{cam[0]*100:.0f} y{cam[1]*100:.0f} z{cam[2]*100:.0f} cm"
    else:
        status = "marker not found"
    for i, line in enumerate((status, click_text)):
        cv2.putText(out, line, (10, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(out, line, (10, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--cam", type=int, default=0)
    src.add_argument("--image", type=Path)
    p.add_argument("--marker-mm", type=float, required=True, help="measured side of the black square")
    p.add_argument("--marker-id", type=int, default=0)
    p.add_argument("--hfov", type=float, default=70.0, help="assumed horizontal field of view, degrees")
    p.add_argument("--no-window", action="store_true", help="annotate one frame and save it")
    p.add_argument("--out", type=Path, default=Path("calibration_check.png"))
    a = p.parse_args()

    import cv2  # noqa: PLC0415
    cap = None
    if a.image:
        still = cv2.imread(str(a.image))
        if still is None:
            raise SystemExit(f"Cannot read {a.image}")
    else:
        cap = cv2.VideoCapture(a.cam)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        if not cap.isOpened():
            raise SystemExit(f"Cannot open camera {a.cam}")

    state = {"cal": None, "text": "click a point on the table"}

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and state["cal"] is not None:
            try:
                xy = state["cal"].pixel_to_table((x, y)) * 100
                state["text"] = f"pixel ({x},{y}) -> table x {xy[0]:+.1f} cm, y {xy[1]:+.1f} cm"
            except ValueError as e:
                state["text"] = f"click rejected: {e}"

    def process(frame):
        h, w = frame.shape[:2]
        corners = detect_marker(frame, a.marker_id)
        state["cal"] = (TableCalibration.from_marker(corners, a.marker_mm / 1000, guess_intrinsics(w, h, a.hfov))
                        if corners is not None else None)
        return annotate(frame, state["cal"], corners, state["text"])

    try:
        if a.no_window:
            frame = still if a.image else next(f for f in (cap.read()[1] for _ in range(10)) if f is not None)
            out = process(frame)
            cv2.imwrite(str(a.out), out)
            cal = state["cal"]
            print("marker not found" if cal is None else
                  f"reprojection {cal.reprojection_px:.2f} px, camera at {np.round(cal.camera_position() * 100, 1)} cm")
            print(f"Saved {a.out}")
            return
        cv2.namedWindow("calibration check")
        cv2.setMouseCallback("calibration check", on_mouse)
        while True:
            if a.image:
                frame = still
            else:
                ok, frame = cap.read()
                if not ok:
                    raise SystemExit("Camera stopped delivering frames")
            out = process(frame)
            cv2.imshow("calibration check", out)
            key = cv2.waitKey(1 if cap else 50) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                cv2.imwrite(str(a.out), out)
                print(f"Saved {a.out}")
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
