"""Object size and orientation from two or three SAM masks, for grasp planning.

Language says *which* object; this says how to hold it. Each camera gives a mask and a `TableCalibration` (the same marker frame as
the sim, the arm and the tracker). We carve a small voxel grid around the object, keep the cells that land inside every mask that can
see them (a visual hull), and read length, width, height and the long-axis yaw off what is left. With one camera we fall back to the
mask's footprint on the table plane. No model, no network: pure numpy plus OpenCV's projection.

The width is an upper bound (view angle and mask edges both fatten the hull), which is the safe side for a jaw-opening check.
Masks must be in each calibration's pixel frame. Frame coordinates are OpenCV's: a pixel's centre is at integer coordinates.
Design and limits: docs/grasp-orientation-plan.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

MIN_VOTES = 2                  # a hull cell must be seen by at least this many views to survive
DEFAULT_ELONGATION = 1.5       # length/width below this: no preferred grasp yaw (a cube)
FOOTPRINT_SAMPLES = 4000


class GeometryError(ValueError):
    """The masks do not describe one sensible object; the grasp must be refused, not guessed."""


@dataclass(frozen=True)
class View:
    calibration: object        # TableCalibration
    mask: object               # (H, W) bool array in the calibration's pixel frame


@dataclass(frozen=True)
class ObjectGeometry:
    centre: tuple[float, float, float]     # marker frame, metres
    long_axis_yaw_deg: float               # long axis in the table plane, [0, 180), x axis = 0, counter-clockwise
    length_m: float
    width_m: float                         # upper bound
    height_m: float
    elongation: float                      # length / width
    views: int
    cells: int                             # hull cells kept (footprint samples for the fallback)
    discarded_fraction: float              # share of the hull dropped as disconnected fragments
    clipped: bool                          # the hull touches the edge of the search box: the object may be larger than it looks
    method: str                            # "hull" or "footprint"


@dataclass(frozen=True)
class GraspPlan:
    yaw_deg: float | None                  # gripper yaw in the table frame, (-90, 90]; None = any yaw will do
    note: str


def oriented_object(obj, views, *, jaw_opening_m: float | None = None, **kwargs):
    """`obj` (a `world.TrackedObject`) with its position, size and grasp yaw taken from the masks of `views`.

    Raises `GeometryError` when the masks disagree, the object is too wide for the jaws or runs off the measured area. The caller must
    then refuse the pick (or say why): falling back to a plain centre grasp would be wrong for exactly the objects this exists for."""
    from dataclasses import replace  # noqa: PLC0415
    geometry = estimate_geometry(views, obj.position[:2], **kwargs)
    plan = plan_grasp(geometry, jaw_opening_m=jaw_opening_m)
    return replace(obj, position=geometry.centre, grasp_yaw_deg=plan.yaw_deg,
                   size_m=(geometry.length_m, geometry.width_m, geometry.height_m))


def _cv2():
    import cv2  # noqa: PLC0415
    return cv2


def _validated(views) -> list[View]:
    views = list(views)
    if not views:
        raise GeometryError("no masks to measure")
    for view in views:
        mask = np.asarray(view.mask)
        if mask.ndim != 2 or mask.dtype != bool:
            raise GeometryError("a mask must be a 2-D boolean array")
        if not mask.any():
            raise GeometryError("a camera's mask is empty")
    return views


def _project(calibration, points: np.ndarray):
    """Pixels (N, 2) and camera-frame depth (N,) of table-frame `points` (N, 3)."""
    cv2 = _cv2()
    pixels, _ = cv2.projectPoints(points, calibration.rvec, calibration.tvec, calibration.camera_matrix, calibration.dist)
    rot = cv2.Rodrigues(calibration.rvec)[0]
    depth = points @ rot[2] + calibration.tvec[2]
    return pixels.reshape(-1, 2), depth


def _pixels_to_plane(calibration, pixels: np.ndarray, z: float) -> np.ndarray:
    """Table-frame (x, y) where each pixel's viewing ray meets the plane at height `z`. Rays that miss it are dropped."""
    cv2 = _cv2()
    rays = cv2.undistortPoints(pixels.reshape(-1, 1, 2).astype(np.float64), calibration.camera_matrix, calibration.dist).reshape(-1, 2)
    rot = cv2.Rodrigues(calibration.rvec)[0]
    directions = np.column_stack([rays, np.ones(len(rays))]) @ rot          # camera -> table frame (R^T applied to row vectors)
    origin = calibration.camera_position()
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = (z - origin[2]) / directions[:, 2]
    good = np.isfinite(scale) & (scale > 0)
    return (origin[:2] + scale[good, None] * directions[good, :2])


def _planar_axes(points_xy: np.ndarray):
    """Centre, long-axis unit vector, and extents (length, width) of 2-D points, from their principal axes."""
    centre = points_xy.mean(axis=0)
    offsets = points_xy - centre
    if len(points_xy) < 3:
        raise GeometryError("too few points to measure")
    values, vectors = np.linalg.eigh(np.cov(offsets.T))
    axis = vectors[:, 1]
    along, across = offsets @ axis, offsets @ vectors[:, 0]
    return centre, axis, float(along.max() - along.min()), float(across.max() - across.min())


def _yaw_deg(axis: np.ndarray) -> float:
    return math.degrees(math.atan2(axis[1], axis[0])) % 180.0


def carve_hull(views, centre_xy, *, half_extent_m: float = 0.10, cell_m: float = 0.002, max_height_m: float = 0.08):
    """The kept cell centres (M, 3) of the visual hull, and the grid indices (M, 2) of their column. Votes need MIN_VOTES views."""
    if cell_m <= 0 or half_extent_m <= cell_m or max_height_m <= cell_m:
        raise GeometryError("bad search box")
    count = int(round(2 * half_extent_m / cell_m))
    axis = (np.arange(count) + 0.5) * cell_m - half_extent_m
    xs, ys = centre_xy[0] + axis, centre_xy[1] + axis
    zs = (np.arange(int(round(max_height_m / cell_m))) + 0.5) * cell_m
    ix, iy, iz = np.meshgrid(np.arange(count), np.arange(count), np.arange(len(zs)), indexing="ij")
    grid = np.column_stack([xs[ix.ravel()], ys[iy.ravel()], zs[iz.ravel()]])
    keep = np.ones(len(grid), bool)
    votes = np.zeros(len(grid), int)
    for view in views:
        mask = np.asarray(view.mask)
        height, width = mask.shape
        pixels, depth = _project(view.calibration, grid)
        u, v = np.rint(pixels[:, 0]).astype(np.int64), np.rint(pixels[:, 1]).astype(np.int64)
        seen = (depth > 0) & (u >= 0) & (u < width) & (v >= 0) & (v < height)
        hit = np.zeros(len(grid), bool)
        hit[seen] = mask[v[seen], u[seen]]
        keep &= ~seen | hit                       # a view that sees the cell and finds it outside its mask vetoes it
        votes += seen
    keep &= votes >= MIN_VOTES
    return grid[keep], np.column_stack([ix.ravel()[keep], iy.ravel()[keep]]), count


def _hull_geometry(views, centre_xy, *, base_height_m: float = 0.006, **box) -> ObjectGeometry:
    """Planar size and yaw come from the hull's lowest layers only. Where the object touches the table there is no parallax, so those
    layers are the object; higher up, two views leave a sheet of "ghost" cells leaning away from the cameras, which would bend the axis
    and fatten the width. Height is how far up the layers stay over that base footprint, an upper bound."""
    cell = box.get("cell_m", 0.002)
    points, columns, count = carve_hull(views, centre_xy, **box)
    if len(points) == 0:
        raise GeometryError("the masks do not overlap in space: the cameras are not looking at the same object")
    cv2 = _cv2()
    layer = np.floor(points[:, 2] / cell).astype(int)
    base = layer <= max(1, int(round(base_height_m / cell)) - 1)
    if not base.any():
        raise GeometryError("the masks do not meet at the table: the object is not resting on it, or the cameras disagree")
    footprint = np.zeros((count, count), np.uint8)
    footprint[columns[base, 0], columns[base, 1]] = 1
    labels_count, labels = cv2.connectedComponents(footprint, connectivity=8)
    sizes = np.bincount(labels[footprint > 0], minlength=labels_count)
    sizes[0] = 0
    piece = (labels == int(sizes.argmax())).astype(np.uint8)
    discarded = 1.0 - float(piece.sum()) / float(footprint.sum())
    base &= piece[columns[:, 0], columns[:, 1]] > 0
    if base.sum() < 5:
        raise GeometryError("the hull is too small to measure")
    centre_, axis, length, width = _planar_axes(points[base, :2])
    clipped = bool(columns[base].min() == 0 or columns[base].max() == count - 1)
    over = cv2.dilate(piece, np.ones((5, 5), np.uint8)) > 0
    height_layers = 0
    for k in range(int(layer.max()) + 1):
        cells = layer == k
        if not cells.any() or over[columns[cells, 0], columns[cells, 1]].mean() < 0.8:
            break
        height_layers = k + 1
    length, width, height = length + cell, width + cell, max(height_layers, 1) * cell
    return ObjectGeometry(
        centre=(float(centre_[0]), float(centre_[1]), height / 2),
        long_axis_yaw_deg=_yaw_deg(axis), length_m=length, width_m=width, height_m=height,
        elongation=length / width, views=len(views), cells=int(base.sum()), discarded_fraction=discarded,
        clipped=clipped, method="hull")


def footprint_geometry(view: View, *, assumed_height_m: float = 0.02) -> ObjectGeometry:
    """One camera: the mask's pixels laid on the plane at half the assumed height. No height measurement, perspective-limited."""
    view = _validated([view])[0]
    ys, xs = np.nonzero(np.asarray(view.mask))
    pick = np.linspace(0, len(xs) - 1, min(FOOTPRINT_SAMPLES, len(xs))).astype(int)
    points = _pixels_to_plane(view.calibration, np.column_stack([xs[pick], ys[pick]]), assumed_height_m / 2)
    if len(points) < 5:
        raise GeometryError("the mask does not meet the table plane")
    centre, axis, length, width = _planar_axes(points)
    width = max(width, 1e-4)
    return ObjectGeometry(
        centre=(float(centre[0]), float(centre[1]), assumed_height_m / 2),
        long_axis_yaw_deg=_yaw_deg(axis), length_m=length, width_m=width, height_m=assumed_height_m,
        elongation=length / width, views=1, cells=int(len(points)), discarded_fraction=0.0, clipped=False, method="footprint")


def estimate_geometry(views, centre_xy=None, *, half_extent_m: float = 0.10, cell_m: float = 0.002, max_height_m: float = 0.08,
                      base_height_m: float = 0.006, assumed_height_m: float = 0.02) -> ObjectGeometry:
    """Size and orientation of one object from its masks. `centre_xy` is the rough table position (e.g. the tracker's); if it is
    not given it is taken from the first mask's footprint. Two or more views give the hull; one gives the footprint fallback."""
    views = _validated(views)
    if len(views) == 1:
        return footprint_geometry(views[0], assumed_height_m=assumed_height_m)
    if centre_xy is None:
        rough = footprint_geometry(views[0], assumed_height_m=assumed_height_m)
        centre_xy = rough.centre[:2]
    return _hull_geometry(views, (float(centre_xy[0]), float(centre_xy[1])), base_height_m=base_height_m,
                          half_extent_m=half_extent_m, cell_m=cell_m, max_height_m=max_height_m)


def plan_grasp(geometry: ObjectGeometry, *, jaw_opening_m: float | None = None, min_elongation: float = DEFAULT_ELONGATION) -> GraspPlan:
    """Where the jaws should point. Raises `GeometryError` when the grasp should be refused rather than attempted."""
    if geometry.clipped:
        raise GeometryError("the object runs off the edge of the measured area: its size is unknown")
    elongated = geometry.elongation >= min_elongation
    across = geometry.width_m if elongated else max(geometry.width_m, geometry.length_m)   # what the jaws must span
    if jaw_opening_m is not None and across > jaw_opening_m:
        raise GeometryError(f"too wide for the gripper: {across * 1000:.0f} mm across, jaws open {jaw_opening_m * 1000:.0f} mm")
    if not elongated:
        return GraspPlan(None, f"compact ({geometry.elongation:.1f}:1): any yaw")
    yaw = (geometry.long_axis_yaw_deg + 90.0) % 180.0
    if yaw > 90.0:
        yaw -= 180.0                           # jaws are symmetric: fold into (-90, 90]
    return GraspPlan(yaw, f"elongated ({geometry.elongation:.1f}:1): jaws across the long axis ({geometry.method}, {geometry.views} view(s))")
