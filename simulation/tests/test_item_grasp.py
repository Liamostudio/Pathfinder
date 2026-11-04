"""Different items end to end in the sim: rendered masks from three cameras -> calibration from the rendered marker -> geometry -> pick.

Items: the red cube (compact), an upright can (compact), a toy duck (elongated body, round bottom) and the pen. The sim's grasp rule is
ours, not physics (see docs/grasp-orientation-plan.md): elongated props need the jaws within 30 degrees of across their long axis.
"""
import math
import time
from dataclasses import replace

import numpy as np
import pytest

from lookandpick.calibration import TableCalibration, detect_marker
from lookandpick.object_geometry import GeometryError, View, oriented_object
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import WorldState
from sim_env.cameras import Cameras
from sim_env.engine import Simulation

AT = (0.20, 0.0)
GRASP_Z = 0.015
CAMERAS = ('camera_a', 'camera_b', 'top')
GEOM = 5                                                    # mjOBJ_GEOM in the segmentation image's second channel
ITEMS = {                                                   # id -> (props to build, the item's centre height, elongated?, across size m)
    'cube': ((), 'obj1', False, 0.036),
    'can': (('can',), 'obj4', False, 0.066),
    'duck': (('duck',), 'obj5', True, 0.050),
    'pen': (('pen',), 'obj3', True, 0.010),
}


class Overlay:
    def __init__(self, sim, overrides):
        self.sim, self.overrides = sim, overrides

    def objects(self):
        return [self.overrides.get(o.id, o) for o in self.sim.objects()]

    def hands(self):
        return self.sim.hands()


def pick_with(sim, item):
    return Tools(WorldState(), Overlay(sim, {item.id: item}), sim, Safety(), say=lambda _: None).pick(item.id)


@pytest.fixture(scope='module', params=list(ITEMS))
def rig(request):
    props, body, elongated, across = ITEMS[request.param]
    sim = Simulation(realtime=False, props=props)
    cameras = Cameras(sim, width=1280, height=720)
    cameras.ready.wait(30)
    assert cameras.error is None, cameras.error
    # The default 'top' view looks almost straight down and the resting arm hides part of the marker; aim it from the side instead.
    cameras.configure('top', {'azimuth': -135., 'elevation': -45., 'distance': 0.65, 'lookat': [0.08, 0, 0.04]})
    yield request.param, sim, cameras, body, elongated, across
    cameras.close(); sim.close()


def put(sim, body, yaw):
    sim.reset()
    if body == 'obj1':
        addr = sim.model.joint('obj1_free').qposadr[0]
        half = math.radians(yaw) / 2
        sim.data.qpos[addr:addr+7] = [*AT, 0.018, math.cos(half), 0, 0, math.sin(half)]
        import mujoco
        mujoco.mj_forward(sim.model, sim.data)
    else:
        sim.place_prop(body, *AT, yaw)


def fresh_frames(cameras, since):
    """Frames rendered from a scene copied after `since` (see test_pen_grasp.fresh_frames for why two waits)."""
    def newer(than):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            frames = [cameras.frame(n) for n in CAMERAS]
            stamps = [f['metadata']['timestamp'] for f in frames]
            if min(stamps) > than:
                return frames, max(stamps)
            time.sleep(0.05)
        raise AssertionError('no fresh frames')
    _, stamp = newer(since)
    return newer(stamp)[0]


def views_from_render(sim, cameras, body, yaw, names=CAMERAS):
    put(sim, body, yaw)
    frames = dict(zip(CAMERAS, fresh_frames(cameras, time.monotonic())))
    body_id = sim.model.body(body).id
    geoms = np.nonzero(sim.model.geom_bodyid == body_id)[0]
    views = []
    for name in names:
        f = frames[name]
        corners = detect_marker(f['rgb'][:, :, ::-1])
        assert corners is not None, name
        cal = TableCalibration.from_marker(corners, 0.12, f['metadata']['K'], f['metadata']['dist'])
        seg = f['segmentation']
        views.append(View(cal, np.isin(seg[:, :, 0], geoms) & (seg[:, :, 1] == GEOM)))
    return views


def across_error(grasp_yaw, yaw):
    return abs((grasp_yaw - (yaw + 90.0) + 90.0) % 180.0 - 90.0)


@pytest.mark.parametrize('yaw', [0, 40, 80, 130])
def test_item_is_measured_planned_and_picked(rig, yaw):
    name, sim, cameras, body, elongated, across = rig
    views = views_from_render(sim, cameras, body, yaw)
    rough = next(o for o in sim.objects() if o.id == body)
    oriented = oriented_object(rough, views, jaw_opening_m=0.10, grasp_z_m=GRASP_Z)
    truth = next(o for o in sim.objects() if o.id == body).position
    assert math.dist(oriented.position[:2], truth[:2]) < 0.012, (name, yaw, oriented.position)
    if name == 'duck':
        # Documented: a duck whose head and body blur the axis can read as compact, and then has no yaw to give. Then the sim, correctly,
        # misses it. What must never happen is a confident wrong yaw.
        if oriented.grasp_yaw_deg is None:
            assert pick_with(sim, oriented).startswith('blocked') and 'no grasp yaw' in sim.reason
            return
        assert across_error(oriented.grasp_yaw_deg, yaw) < 25.0, (yaw, oriented.grasp_yaw_deg)
    elif elongated:
        assert oriented.grasp_yaw_deg is not None and across_error(oriented.grasp_yaw_deg, yaw) < 8.0, (name, yaw, oriented.grasp_yaw_deg)
    else:
        assert oriented.grasp_yaw_deg is None, (name, yaw, oriented.grasp_yaw_deg)      # compact: the planner asks for no particular yaw
    assert pick_with(sim, oriented) == f'picked {rough.label}', sim.reason


def test_measured_size_is_never_below_the_item_for_compact_items(rig):
    name, sim, cameras, body, elongated, across = rig
    if elongated:
        pytest.skip('the jaws only have to span the short side of an elongated item')
    views = views_from_render(sim, cameras, body, 25)
    from lookandpick.object_geometry import estimate_geometry
    g = estimate_geometry(views, AT, max_height_m=0.14)
    assert max(g.width_m, g.length_m, g.silhouette_width_m) >= across - 0.004, (name, g)
    assert max(g.width_m, g.length_m) < across + 0.035, (name, g)


def test_toy_duck_success_rate_is_what_the_docs_say(rig):
    """Documented, not hidden: with the default elongation threshold (1.5, chosen so cubes and balls never get a spurious wrist turn) a
    duck whose body and head blur its axis is sometimes read as compact, gets no yaw, and the sim correctly misses it. Over nine yaws
    7 are picked and 2 missed; a yaw that is given is always within 25 degrees of across."""
    name, sim, cameras, body, elongated, across = rig
    if name != 'duck':
        pytest.skip('duck only')
    picked = missed = 0
    for yaw in range(0, 180, 22):
        views = views_from_render(sim, cameras, body, yaw)
        rough = next(o for o in sim.objects() if o.id == body)
        oriented = oriented_object(rough, views, jaw_opening_m=0.10, grasp_z_m=GRASP_Z)
        if oriented.grasp_yaw_deg is not None:
            assert across_error(oriented.grasp_yaw_deg, yaw) < 25.0, (yaw, oriented.grasp_yaw_deg)
        if pick_with(sim, oriented).startswith('picked'):
            picked += 1
        else:
            missed += 1
            assert oriented.grasp_yaw_deg is None                      # a miss is only ever the 'no yaw given' case, never a wrong yaw
    assert picked >= 6 and missed <= 3, (picked, missed)
