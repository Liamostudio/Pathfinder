"""Different household items through the same fused-mask geometry: cubes, cans, a ball, small toys. Synthetic views, known truth.

What holds and what does not is written down in docs/grasp-orientation-plan.md ("Other items"). Short version: vertical-walled objects
(cubes, an upright can, a pen) read slightly big, which is the safe side; rounded-bottomed ones (ball, duck) read narrow in the base
layers, so the silhouette width is the floor for the jaw check; two views are not enough for a toy that is not clearly elongated.
"""
import math

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.object_geometry import GeometryError, View, estimate_geometry, plan_grasp  # noqa: E402
from test_object_geometry import CAM_A, CAM_B, CAM_C, SIZE, angle_error  # noqa: E402

CENTRE = (0.12, 0.06)
YAWS = (0, 30, 60, 90, 120, 150)
TWO, THREE = (CAM_A, CAM_B), (CAM_A, CAM_B, CAM_C)


def rotated(points, yaw):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return points @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T + np.array([CENTRE[0], CENTRE[1], 0.0])


def box(l, w, h, z0=0.0, off=(0.0, 0.0)):
    return np.array([(x + off[0], y + off[1], z) for x in (-l / 2, l / 2) for y in (-w / 2, w / 2) for z in (z0, z0 + h)])


def can(r, h, lying=False, n=48):
    angles = np.linspace(0, 2 * math.pi, n, endpoint=False)
    if lying:                                            # axis along x, resting on its side
        return np.array([(x, r * math.cos(t), r + r * math.sin(t)) for t in angles for x in (-h / 2, h / 2)])
    return np.array([(r * math.cos(t), r * math.sin(t), z) for t in angles for z in (0.0, h)])


def ellipsoid(a, b, c, z, off=(0.0, 0.0), n=40):
    return np.array([(off[0] + a * math.sin(u) * math.cos(v), off[1] + b * math.sin(u) * math.sin(v), z + c * math.cos(u))
                     for u in np.linspace(0, math.pi, n) for v in np.linspace(0, 2 * math.pi, 2 * n, endpoint=False)])


def mask_of(cal, parts):
    """Union of the filled convex hulls of each (convex) part's projected vertices."""
    mask = np.zeros((SIZE[1], SIZE[0]), np.uint8)
    for part in parts:
        pixels, _ = cv2.projectPoints(part, cal.rvec, cal.tvec, cal.camera_matrix, cal.dist)
        cv2.fillConvexPoly(mask, cv2.convexHull(np.rint(pixels.reshape(-1, 2)).astype(np.int32)), 1)
    return mask.astype(bool)


# name -> (parts, true body-axis yaw offset or None if compact, true across-the-jaws size in metres)
ITEMS = {
    "cube 30 mm": ([box(.03, .03, .03)], None, .030),
    "cube 45 mm": ([box(.045, .045, .045)], None, .045),
    "can 66 mm upright": ([can(.033, .10)], None, .066),
    "ball 40 mm": ([ellipsoid(.02, .02, .02, .02)], None, .040),
    "toy car": ([box(.07, .035, .02, z0=.008), box(.035, .03, .015, z0=.028, off=(-.005, 0))], 0.0, .035),
    "toy duck": ([ellipsoid(.04, .025, .02, .02), ellipsoid(.015, .015, .015, .048, off=(.036, 0))], 0.0, .050),
    "can 66 mm on its side": ([can(.033, .10, lying=True)], 0.0, .066),
}


def views_for(name, yaw, cams):
    placed = [rotated(p, yaw) for p in ITEMS[name][0]]
    return [View(c, mask_of(c, placed)) for c in cams]


def measure(name, yaw, cams, **kw):
    return estimate_geometry(views_for(name, yaw, cams), CENTRE, **kw)


@pytest.mark.parametrize("name", ["cube 30 mm", "cube 45 mm", "can 66 mm upright", "ball 40 mm"])
@pytest.mark.parametrize("cams,slack", [(TWO, 0.025), (THREE, 0.018)], ids=["2 views", "3 views"])
def test_compact_items_are_found_centred_and_sized_on_the_safe_side(name, cams, slack):
    true = ITEMS[name][2]
    for yaw in YAWS:
        g = measure(name, yaw, cams, max_height_m=0.14)
        assert math.hypot(g.centre[0] - CENTRE[0], g.centre[1] - CENTRE[1]) < 0.008, (name, yaw)
        assert g.centre_on_object and not g.clipped
        assert max(g.width_m, g.length_m, g.silhouette_width_m) >= true - 0.003, (name, yaw)         # never smaller than the thing
        assert max(g.width_m, g.length_m) <= true * 1.45 + slack, (name, yaw, g.length_m, g.width_m)  # and not absurdly bigger


@pytest.mark.parametrize("name", ["cube 30 mm", "cube 45 mm", "can 66 mm upright", "ball 40 mm"])
def test_compact_items_get_no_yaw_with_three_views_and_are_never_refused_by_a_roomy_gripper(name):
    for yaw in YAWS:
        g = measure(name, yaw, THREE, max_height_m=0.14)
        assert plan_grasp(g, jaw_opening_m=0.10).yaw_deg is None, (name, yaw, g.elongation)
    for yaw in YAWS:                                       # two views may read a ball as slightly elongated: a yaw is harmless, a refusal is not
        plan_grasp(measure(name, yaw, TWO, max_height_m=0.14), jaw_opening_m=0.10)


def test_the_jaw_check_uses_the_real_size_even_for_a_round_object():
    """The ball's base layers read ~25 mm wide for a 40 mm ball; the silhouette floor stops a 32 mm gripper being told it fits."""
    for yaw in (0, 60):
        g = measure("ball 40 mm", yaw, THREE)
        assert g.width_m < 0.036 or g.length_m < 0.036                      # the under-read is real (see the plan doc)
        assert g.silhouette_width_m >= 0.036
        with pytest.raises(GeometryError, match="too wide"):
            plan_grasp(g, jaw_opening_m=0.032)
        plan_grasp(g, jaw_opening_m=0.06)


def test_a_can_is_refused_by_jaws_that_are_too_small_and_taken_by_roomy_ones():
    g = measure("can 66 mm upright", 0, THREE, max_height_m=0.14)
    with pytest.raises(GeometryError, match="too wide"):
        plan_grasp(g, jaw_opening_m=0.05)
    plan_grasp(g, jaw_opening_m=0.09)


def test_a_tall_item_is_flagged_when_it_runs_past_the_search_box():
    assert measure("can 66 mm upright", 0, THREE).height_clipped                          # 100 mm can, default box is 80 mm
    assert not measure("can 66 mm upright", 0, THREE, max_height_m=0.14).height_clipped
    assert not measure("cube 30 mm", 0, THREE).height_clipped


@pytest.mark.parametrize("name", ["toy car", "can 66 mm on its side"])
def test_elongated_items_get_jaws_across_the_body_with_three_views(name):
    for yaw in YAWS:
        g = measure(name, yaw, THREE)
        plan = plan_grasp(g, jaw_opening_m=0.09)
        assert plan.yaw_deg is not None, (name, yaw, g.elongation)
        assert angle_error(plan.yaw_deg, yaw + 90.0) < 12.0, (name, yaw, plan.yaw_deg)


def test_the_toy_duck_never_gets_a_wrong_yaw_but_may_be_read_as_compact():
    """Head and body blur the axis: with three views a yaw, when given, is within 15 degrees; 2 of 6 poses may come back with none."""
    given = 0
    for yaw in YAWS:
        g = measure("toy duck", yaw, THREE)
        plan = plan_grasp(g, jaw_opening_m=0.10)
        if plan.yaw_deg is not None:
            given += 1
            assert angle_error(plan.yaw_deg, yaw + 90.0) < 15.0, (yaw, plan.yaw_deg)
    assert given >= 4


def test_two_views_are_not_enough_for_the_duck_or_a_lying_can():
    """Documented limit, not a goal: with two views the axis of a fat, rounded item can be 15-30 degrees out. This pins the behaviour
    so an improvement (or a regression) is noticed; the planner's answer for such items is 'use three views'."""
    worst = {}
    for name in ("toy duck", "can 66 mm on its side"):
        errors = []
        for yaw in YAWS:
            try:
                plan = plan_grasp(measure(name, yaw, TWO), jaw_opening_m=0.10)
            except GeometryError:
                continue
            if plan.yaw_deg is not None:
                errors.append(angle_error(plan.yaw_deg, yaw + 90.0))
        worst[name] = max(errors) if errors else 0.0
    assert max(worst.values()) > 10.0, worst


def test_known_limit_an_l_shaped_toy_is_only_sometimes_caught():
    """Silhouettes cannot see into a notch: the hull fills it, the size is inflated (115 x 73 mm for an 80 x 80 mm L) and the measured
    centre lands ~10 mm off the material at every pose. `centre_on_object` refuses only the poses where the hull still shows the gap
    (4 of 12 here). Pinned so the limit is visible; a concave item needs depth or a contact check, not more silhouettes."""
    arms = [box(.08, .015, .02, off=(0, -.0325)), box(.015, .08, .02, off=(-.0325, 0))]

    def gap_to_material(point, yaw):
        c, s = math.cos(math.radians(-yaw)), math.sin(math.radians(-yaw))
        x, y = point[0] - CENTRE[0], point[1] - CENTRE[1]
        lx, ly = c * x - s * y, s * x + c * y
        return min(math.hypot(max(x0 - lx, 0, lx - x1), max(y0 - ly, 0, ly - y1))
                   for x0, x1, y0, y1 in ((-.04, .04, -.04, -.025), (-.04, -.025, -.04, .04)))

    refused = total = 0
    for cams in (TWO, THREE):
        for yaw in YAWS:
            placed = [rotated(p, yaw) for p in arms]
            g = estimate_geometry([View(c, mask_of(c, placed)) for c in cams], CENTRE)
            total += 1
            assert gap_to_material(g.centre, yaw) < 0.015, yaw                 # bounded: never wildly off
            try:
                plan_grasp(g, jaw_opening_m=0.15)
            except GeometryError as error:
                refused += 1
                assert "not on the object" in str(error)
    assert 0 < refused < total, (refused, total)                              # caught sometimes, not reliably


def test_masks_of_a_can_in_one_camera_and_a_cube_in_the_other_are_refused():
    a = views_for("can 66 mm upright", 0, (CAM_A,))[0]
    far = [rotated(p, 0) + np.array([0.09, -0.05, 0.0]) for p in ITEMS["cube 30 mm"][0]]     # a cube elsewhere on the table
    b = View(CAM_B, mask_of(CAM_B, far))
    with pytest.raises(GeometryError):
        estimate_geometry([a, b], CENTRE, max_height_m=0.14)
