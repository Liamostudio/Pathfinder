"""A top-down, metric texture of the table plane from ONE photo that shows the ArUco marker.

The marker's four corners define the table plane exactly (a homography from the marker's metres to the image's pixels), so the result has a
perfectly square marker and square checkerboard squares whatever the camera pose or its (unknown) lens. Heights above the plane are not
corrected: use it for the flat table top, not for objects. Visual only; never a safety input.

  PYTHONPATH=. workspace_splat/.venv/bin/python -m lookandpick.table_texture photo.jpg out.png --extent -0.95 0.30 -0.42 0.36 --mm-per-px 0.75
Writes out.png and out.json (the extent in the marker frame, x right, y up, in metres, and the scale), which is what a renderer needs to put it on the table.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from .calibration import detect_marker


def rectify(image: np.ndarray, corners, marker_m: float, extent: tuple[float, float, float, float], mm_per_px: float = 1.0) -> np.ndarray:
    """Warp `image` to the marker plane. `corners` are the marker's four pattern corners as `detect_marker` returns them (TL, TR, BR, BL of the
    pattern); `extent` = (x0, x1, y0, y1) metres in the marker frame (origin at the marker centre, x right, y up)."""
    x0, x1, y0, y1 = extent
    if not (x1 > x0 and y1 > y0 and mm_per_px > 0 and marker_m > 0):
        raise ValueError("need x1 > x0, y1 > y0, a positive scale and a positive marker size")
    width, height = int(round((x1 - x0) * 1000 / mm_per_px)), int(round((y1 - y0) * 1000 / mm_per_px))
    if not (0 < width <= 8000 and 0 < height <= 8000):
        raise ValueError("the output would be larger than 8000 pixels on a side; widen mm_per_px")
    h = marker_m / 2
    to_image = cv2.getPerspectiveTransform(np.float32([[-h, h], [h, h], [h, -h], [-h, -h]]), np.asarray(corners, np.float32).reshape(4, 2))
    metres_per_px = mm_per_px / 1000
    pixel_to_metres = np.array([[metres_per_px, 0, x0], [0, -metres_per_px, y1], [0, 0, 1]], float)
    return cv2.warpPerspective(image, to_image @ pixel_to_metres, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", type=Path)
    p.add_argument("output", type=Path, help="PNG to write; a .json with the extent is written beside it")
    p.add_argument("--marker-m", type=float, default=0.12, help="the black square's measured side, metres")
    p.add_argument("--extent", type=float, nargs=4, metavar=("X0", "X1", "Y0", "Y1"), default=(-0.95, 0.30, -0.42, 0.36))
    p.add_argument("--mm-per-px", type=float, default=0.75)
    a = p.parse_args(argv)
    image = cv2.imread(str(a.image))
    if image is None:
        raise SystemExit(f"cannot read {a.image}")
    corners = detect_marker(image)
    if corners is None:
        raise SystemExit("the ArUco marker (id 0) is not visible in this photo")
    out = rectify(image, corners, a.marker_m, tuple(a.extent), a.mm_per_px)
    cv2.imwrite(str(a.output), out)
    a.output.with_suffix(".json").write_text(json.dumps({"frame": "marker (origin at its centre, x right, y up), metres", "extent": list(a.extent), "mm_per_px": a.mm_per_px,
                                                          "marker_m": a.marker_m, "source": a.image.name, "size_px": [out.shape[1], out.shape[0]]}, indent=1))
    print(f"wrote {a.output} ({out.shape[1]}x{out.shape[0]} px)")


if __name__ == "__main__":
    main()
