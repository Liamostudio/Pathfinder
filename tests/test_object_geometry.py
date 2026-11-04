"""Object size and orientation from fused masks, against synthetic views with known ground truth (no hardware, no model)."""
import math

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.calibration import TableCalibration  # noqa: E402
from lookandpick.object_geometry import (GeometryError, View, estimate_geometry, footprint_geometry,  # noqa: E402
                                         plan_grasp)

K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]])
SIZE = (1280, 720)
PEN = (0.14, 0.010, 0.010)       # length, width, height in metres: a pen lying on the table
CUBE = (0.03, 0.03, 0.03)


def look_at(pos, target=(0.0, 0.0, 0.0)):
    pos, target = np.asarray(pos, float), np.asarray(target, float)
    z = target - pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rot = np.vstack([x, y, z])
    return cv2.Rodrigues(rot)[0].reshape(3), -rot @ pos


def camera(pos, target=(0.0, 0.0, 0.0)):
    rvec, tvec = look_at(pos, target)
    return TableCalibration(K, rvec, tvec)


CAM_A, CAM_B, CAM_C = camera((0.0, -0.55, 0.45)), camera((0.55, 0.0, 0.45)), camera((-0.45, -0.30, 0.50))


def box_corners(centre_xy, yaw_deg, dims):
    length, width, height = dims
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    corners = []
    for dx in (-length / 2, length / 2):
        for dy in (-width / 2, width / 2):
            for z in (0.0, height):
                corners.append((centre_xy[0] + c * dx - s * dy, centre_xy[1] + s * dx + c * dy, z))
    return np.array(corners, float)


def render_mask(cal, corners):
    """The silhouette of a convex solid: the filled hull of its projected corners."""
    pixels, _ = cv2.projectPoints(corners, cal.rvec, cal.tvec, cal.camera_matrix, cal.dist)
    hull = cv2.convexHull(np.rint(pixels.reshape(-1, 2)).astype(np.int32))
    mask = np.zeros((SIZE[1], SIZE[0]), np.uint8)
    cv2.fillConvexPoly(mask, hull, 1)
    return mask.astype(bool)


def views_of(corners, cams=(CAM_A, CAM_B)):
    return [View(cam, render_mask(cam, corners)) for cam in cams]


def angle_error(a, b):
    """Smallest difference between two axis angles (an axis has no direction: 180 degrees apart is the same)."""
    return abs((a - b + 90.0) % 180.0 - 90.0)


@pytest.mark.parametrize("yaw", range(0, 180, 15))
def test_two_views_recover_pen_angle_and_centre(yaw):
    centre = (0.12, 0.06)
    geometry = estimate_geometry(views_of(box_corners(centre, yaw, PEN)), centre)
    assert geometry.method == "hull" and geometry.views == 2
    assert angle_error(geometry.long_axis_yaw_deg, yaw) < 3.0
    assert math.hypot(geometry.centre[0] - centre[0], geometry.centre[1] - centre[1]) < 0.005
    assert not geometry.clipped


def test_pen_size_is_measured_and_width_is_an_upper_bound():
    centre = (0.12, 0.06)
    for yaw in (0, 30, 60, 90, 120, 150):
        g = estimate_geometry(views_of(box_corners(centre, yaw, PEN)), centre)
        assert abs(g.length_m - PEN[0]) < 0.02, (yaw, g.length_m)
        assert PEN[1] - 0.001 <= g.width_m < 0.030, (yaw, g.width_m)       # never under-reads; stays well under a jaw opening
        assert PEN[2] - 0.001 <= g.height_m < 0.035, (yaw, g.height_m)
        assert g.elongation > 3


def test_three_views_are_no_worse_than_two():
    centre = (0.10, -0.05)
    for yaw in (10, 55, 100, 145):
        corners = box_corners(centre, yaw, PEN)
        two = estimate_geometry(views_of(corners), centre)
        three = estimate_geometry(views_of(corners, (CAM_A, CAM_B, CAM_C)), centre)
        assert three.views == 3
        assert angle_error(three.long_axis_yaw_deg, yaw) <= angle_error(two.long_axis_yaw_deg, yaw) + 1.0
        assert three.width_m <= two.width_m + 0.002


def test_rough_centre_can_be_off_by_a_few_centimetres_or_missing():
    truth = (0.12, 0.06)
    views = views_of(box_corners(truth, 40, PEN))
    off = estimate_geometry(views, (truth[0] + 0.04, truth[1] - 0.03))
    assert angle_error(off.long_axis_yaw_deg, 40) < 3.0
    missing = estimate_geometry(views)                                   # falls back to the first mask's footprint for the box centre
    assert angle_error(missing.long_axis_yaw_deg, 40) < 3.0


def test_single_view_falls_back_to_the_footprint():
    centre = (0.12, 0.06)
    for yaw in (0, 45, 90, 135):
        g = estimate_geometry(views_of(box_corners(centre, yaw, PEN), (CAM_A,)), centre)
        assert g.method == "footprint" and g.views == 1
        assert angle_error(g.long_axis_yaw_deg, yaw) < 8.0                # looser: no second view to correct perspective
        assert math.hypot(g.centre[0] - centre[0], g.centre[1] - centre[1]) < 0.02


def test_cube_is_compact_and_gets_no_preferred_yaw():
    centre = (0.12, 0.06)
    for yaw in (0, 30, 60):
        g = estimate_geometry(views_of(box_corners(centre, yaw, CUBE)), centre)
        assert g.elongation < 1.5
        assert plan_grasp(g).yaw_deg is None


def test_pen_gets_jaws_across_the_long_axis():
    centre = (0.12, 0.06)
    for yaw in (0, 20, 70, 90, 110, 160):
        g = estimate_geometry(views_of(box_corners(centre, yaw, PEN)), centre)
        plan = plan_grasp(g, jaw_opening_m=0.05)
        assert plan.yaw_deg is not None and -90.0 < plan.yaw_deg <= 90.0
        assert angle_error(plan.yaw_deg, yaw + 90.0) < 3.0


def test_object_wider_than_the_jaws_is_refused():
    centre = (0.12, 0.06)
    g = estimate_geometry(views_of(box_corners(centre, 30, CUBE)), centre)
    with pytest.raises(GeometryError, match="too wide"):
        plan_grasp(g, jaw_opening_m=0.02)
    plan_grasp(g, jaw_opening_m=0.06)                                    # and the same object passes with wider jaws


def test_masks_of_different_objects_are_refused():
    """Camera A sees a pen here, camera B a pen over there: the hull is empty or in pieces, never a confident answer."""
    here, there = (0.10, 0.05), (0.25, -0.05)
    a = View(CAM_A, render_mask(CAM_A, box_corners(here, 30, PEN)))
    b = View(CAM_B, render_mask(CAM_B, box_corners(there, 30, PEN)))
    with pytest.raises(GeometryError):
        estimate_geometry([a, b], here)


def test_object_at_the_edge_of_the_search_box_is_flagged_and_refused():
    centre = (0.12, 0.06)
    long_pen = (0.30, 0.010, 0.010)
    g = estimate_geometry(views_of(box_corners(centre, 0, long_pen)), centre)
    assert g.clipped
    with pytest.raises(GeometryError, match="edge"):
        plan_grasp(g)


def test_speckle_and_edge_growth_degrade_gracefully():
    rng = np.random.default_rng(3)
    centre = (0.12, 0.06)
    for yaw in (15, 75, 135):
        noisy = []
        for view in views_of(box_corners(centre, yaw, PEN)):
            mask = cv2.dilate(view.mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)      # edges 1 px too generous
            holes = rng.random(mask.shape) < 0.01
            noisy.append(View(view.calibration, mask & ~holes))                                         # and 1% of pixels missing
        g = estimate_geometry(noisy, centre)
        assert angle_error(g.long_axis_yaw_deg, yaw) < 5.0
        assert g.width_m < 0.035


def test_a_pixel_of_calibration_error_does_not_break_the_angle():
    centre = (0.12, 0.06)
    corners = box_corners(centre, 50, PEN)
    shifted = TableCalibration(K, CAM_B.rvec + np.array([0.0, 0.002, 0.0]), CAM_B.tvec)                  # ~1.6 px of aim error
    views = [View(CAM_A, render_mask(CAM_A, corners)), View(shifted, render_mask(CAM_B, corners))]
    g = estimate_geometry(views, centre)
    assert angle_error(g.long_axis_yaw_deg, 50) < 6.0


def test_bad_inputs_raise_geometry_errors():
    with pytest.raises(GeometryError):
        estimate_geometry([])
    with pytest.raises(GeometryError):
        estimate_geometry([View(CAM_A, np.zeros((720, 1280), bool)), View(CAM_B, np.zeros((720, 1280), bool))], (0.1, 0.1))
    with pytest.raises(GeometryError):
        estimate_geometry([View(CAM_A, np.ones((720, 1280), np.uint8))])                                 # not boolean
    with pytest.raises(GeometryError):
        footprint_geometry(View(CAM_A, np.zeros((720, 1280), bool)))


def test_lens_distortion_is_handled():
    """The Ace Pro lens is strongly distorted: project and back-project through the distortion model, not around it."""
    dist = np.array([-0.30, 0.10, 0.0, 0.0, 0.0])
    cams = [TableCalibration(K, CAM_A.rvec, CAM_A.tvec, dist), TableCalibration(K, CAM_B.rvec, CAM_B.tvec, dist)]
    centre = (0.12, 0.06)
    for yaw in (0, 40, 95, 150):
        corners = box_corners(centre, yaw, PEN)
        g = estimate_geometry(views_of(corners, cams), centre)
        assert angle_error(g.long_axis_yaw_deg, yaw) < 3.0
        assert math.hypot(g.centre[0] - centre[0], g.centre[1] - centre[1]) < 0.006
        single = estimate_geometry(views_of(corners, cams[:1]), centre)
        assert angle_error(single.long_axis_yaw_deg, yaw) < 8.0
