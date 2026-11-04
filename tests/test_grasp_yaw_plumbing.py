"""The measured grasp yaw reaches an arm that can take one, and changes nothing for objects without it (cubes, every older arm)."""
import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import TrackedObject, WorldState


def tools_for(objects, arm=None):
    arm = arm or SimArm()
    return Tools(WorldState(), FakeTracker(objects), arm, Safety(), say=lambda _: None), arm


class PlainArm(SimArm):
    """An arm with no yaw support, like the real SO-101 adapter today."""
    set_grasp_yaw = None


def test_pick_hands_the_yaw_to_the_arm_before_it_moves():
    tools, arm = tools_for([TrackedObject("pen", "pen", (0.2, 0.05, 0.005), grasp_yaw_deg=-35.0, size_m=(0.14, 0.01, 0.01))])
    assert tools.pick("pen") == "picked pen"
    assert arm.log[0] == ("grasp_yaw", -35.0) and arm.grasp_yaw_deg == -35.0


def test_a_cube_clears_any_stale_yaw():
    arm = SimArm()
    arm.grasp_yaw_deg = 40.0
    tools, arm = tools_for([TrackedObject("cube", "red cube", (0.2, 0.05, 0.018))], arm)
    assert tools.pick("cube") == "picked red cube"
    assert arm.grasp_yaw_deg is None


def test_an_arm_without_yaw_support_picks_exactly_as_before():
    tools, arm = tools_for([TrackedObject("pen", "pen", (0.2, 0.05, 0.005), grasp_yaw_deg=10.0)], PlainArm())
    assert tools.pick("pen") == "picked pen"
    assert not any(kind == "grasp_yaw" for kind, _ in arm.log)


def test_the_scene_only_shows_orientation_once_it_is_measured():
    tools, _ = tools_for([TrackedObject("cube", "red cube", (0.2, 0.05, 0.018)),
                          TrackedObject("pen", "pen", (0.1, 0.0, 0.005), grasp_yaw_deg=12.5, size_m=(0.14, 0.01, 0.01))])
    by_id = {o["id"]: o for o in tools.get_scene()["objects"]}
    assert set(by_id["cube"]) == {"id", "label", "position", "held"}
    assert by_id["pen"]["grasp_yaw_deg"] == 12.5 and by_id["pen"]["size_m"] == (0.14, 0.01, 0.01)


def test_a_held_object_keeps_its_measurements_through_the_pick():
    tools, _ = tools_for([TrackedObject("pen", "pen", (0.2, 0.05, 0.005), grasp_yaw_deg=-35.0, size_m=(0.14, 0.01, 0.01))])
    tools.pick("pen")
    held = next(o for o in tools.get_scene()["objects"] if o["id"] == "pen")
    assert held["held"] and held["grasp_yaw_deg"] == -35.0


def test_oriented_object_from_masks():
    pytest.importorskip("cv2")
    from test_object_geometry import CAM_A, CAM_B, PEN, box_corners, views_of  # noqa: PLC0415
    from lookandpick.object_geometry import GeometryError, oriented_object  # noqa: PLC0415
    rough = TrackedObject("pen-1", "pen", (0.13, 0.04, 0.01))                  # a single-view tracker position, a little off
    oriented = oriented_object(rough, views_of(box_corners((0.12, 0.06), 30, PEN), (CAM_A, CAM_B)), jaw_opening_m=0.05)
    assert oriented.id == "pen-1" and abs(oriented.position[0] - 0.12) < 0.005 and abs(oriented.position[1] - 0.06) < 0.005
    assert abs(oriented.grasp_yaw_deg - (-60.0)) < 3.0 and oriented.size_m[0] > 0.12
    with pytest.raises(GeometryError, match="too wide"):
        oriented_object(rough, views_of(box_corners((0.12, 0.06), 30, PEN), (CAM_A, CAM_B)), jaw_opening_m=0.005)
