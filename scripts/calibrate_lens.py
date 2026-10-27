"""Calibrate one camera's lens with the printed checkerboard and save it to calibration/<name>.json.

    python scripts/make_marker.py --checkerboard        # print markers/checkerboard_6x9_20mm.pdf at 100%
    python scripts/calibrate_lens.py --cam 2 --name ace-pro --square-mm 20

Stick the board flat on something rigid, hold it in front of the camera and move it around: near
the corners and edges, tilted both ways, close and far. A view is taken automatically each time
the board is held still somewhere new; 20 views is plenty. Press c to calibrate early, q to quit.
Use --rational for very wide lenses if the error stays above ~0.5 px. Calibration is only valid at
the resolution it was captured at, so use the same --width/--height later. Needs the `vision` extra.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from lookandpick.intrinsics import CALIBRATION_DIR, calibrate, find_board


def coverage_warning(views, size) -> str | None:
    pts = np.vstack(views)
    w, h = size
    missing = [name for name, bad in (("left", pts[:, 0].min() > 0.2 * w), ("right", pts[:, 0].max() < 0.8 * w),
                                      ("top", pts[:, 1].min() > 0.2 * h), ("bottom", pts[:, 1].max() < 0.8 * h)) if bad]
    return f"views never reach the {', '.join(missing)} of the image; the edges (most distorted) are unconstrained" if missing else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--name", required=True, help="file name stem, e.g. ace-pro")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--cols", type=int, default=6, help="inner corners across")
    p.add_argument("--rows", type=int, default=9, help="inner corners down")
    p.add_argument("--square-mm", type=float, default=20.0, help="measured side of one printed square")
    p.add_argument("--views", type=int, default=20)
    p.add_argument("--rational", action="store_true")
    p.add_argument("--out-dir", type=Path, default=CALIBRATION_DIR)
    a = p.parse_args()

    import cv2  # noqa: PLC0415
    cap = cv2.VideoCapture(a.cam, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open camera {a.cam}")

    views: list[np.ndarray] = []
    last_seen, last_taken, tick, flash = None, None, 0, 0
    size = None
    try:
        cv2.namedWindow("lens calibration")
        while len(views) < a.views:
            ok, frame = cap.read()
            if not ok:
                raise SystemExit("Camera stopped delivering frames")
            size = (frame.shape[1], frame.shape[0])
            tick += 1
            corners = find_board(frame, a.cols, a.rows) if tick % 3 == 0 else None
            if corners is not None:
                held = last_seen is not None and np.abs(corners - last_seen).mean() < 2.0
                moved = last_taken is None or np.abs(corners - last_taken).mean() > 50
                if held and moved:
                    views.append(corners)
                    last_taken, flash = corners, 6
                last_seen = corners
                cv2.drawChessboardCorners(frame, (a.cols, a.rows), corners.astype(np.float32).reshape(-1, 1, 2), True)
            elif tick % 3 == 0:
                last_seen = None
            if flash:
                cv2.rectangle(frame, (0, 0), (size[0] - 1, size[1] - 1), (0, 255, 0), 12)
                flash -= 1
            cv2.putText(frame, f"{len(views)}/{a.views} views  (c = calibrate now, q = quit)", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("lens calibration", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                return
            if key == ord("c") and len(views) >= 8:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    cal = calibrate(views, a.cols, a.rows, a.square_mm / 1000, size, rational=a.rational)
    path = a.out_dir / f"{a.name}.json"
    cal.save(path)
    fx = cal.camera_matrix[0, 0]
    hfov = float(np.degrees(2 * np.arctan(size[0] / 2 / fx)))
    print(f"{len(views)} views, reprojection RMS {cal.rms_px:.2f} px, focal {fx:.0f} px (~{hfov:.0f} deg horizontal at the centre)")
    print("Good" if cal.rms_px < 0.7 else "RMS is high: retake with a flatter board, or try --rational")
    warning = coverage_warning(views, size)
    if warning:
        print("Warning:", warning)
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
