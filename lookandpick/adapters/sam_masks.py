"""Turn SAM 3.1's raw outputs into small, plain detections (pixel coordinates in the ORIGINAL image). numpy only, no model."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Detection:
    score: float
    box: tuple[float, float, float, float]       # x0, y0, x1, y1
    centroid: tuple[float, float]                # x, y of the mask's centre of mass
    bottom: tuple[float, float]                  # x of the lowest mask pixels' mean, and that lowest y
    area: int                                    # mask pixels in the ORIGINAL image's scale (rounded)


def detections_from_outputs(outputs: dict | None, image_size: tuple[int, int], *, min_area_px: int = 20) -> list[Detection]:
    """`outputs` is SAM's per-frame dict: `out_binary_masks` (N, H, W) and optionally `out_probs` (N,).

    Masks may be at the model's resolution; coordinates are scaled to `image_size` (width, height). Empty or tiny masks are dropped.
    Detections come back best score first."""
    outputs = outputs or {}
    masks = outputs.get("out_binary_masks")
    if masks is None:
        return []
    masks = np.asarray(masks.cpu() if hasattr(masks, "cpu") else masks)
    if masks.ndim == 4 and masks.shape[1] == 1:
        masks = masks[:, 0]
    if masks.ndim != 3 or masks.shape[0] == 0:
        return []
    probs = outputs.get("out_probs")
    probs = np.ones(masks.shape[0]) if probs is None else np.asarray(probs.cpu() if hasattr(probs, "cpu") else probs, dtype=float).reshape(-1)
    width, height = image_size
    sx, sy = width / masks.shape[2], height / masks.shape[1]
    found = []
    for k in range(masks.shape[0]):
        ys, xs = np.nonzero(masks[k])
        area = int(round(len(xs) * sx * sy))
        if len(xs) == 0 or area < min_area_px:
            continue
        low = ys.max()
        bottom_x = xs[ys >= low - 1].mean()
        found.append(Detection(float(probs[k]) if k < len(probs) else 1.0,
                               (float(xs.min() * sx), float(ys.min() * sy), float((xs.max() + 1) * sx), float((ys.max() + 1) * sy)),
                               (float((xs.mean() + 0.5) * sx), float((ys.mean() + 0.5) * sy)),
                               (float((bottom_x + 0.5) * sx), float((low + 1) * sy)), area))
    return sorted(found, key=lambda d: -d.score)
