"""The opt-in sim pen: a pick succeeds only when the jaws are across it, and masks rendered by the sim drive the whole chain."""
import math
import time

import numpy as np
import pytest

from lookandpick.calibration import TableCalibration, detect_marker
from lookandpick.object_geometry import GeometryError, View, oriented_object
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import WorldState
from sim_env.cameras import Cameras
from sim_env.engine import Simulation

PEN_AT = (0.20, 0.0)
GRASP_Z = 0.015                  # the sim's workspace floor


class Overlay:
    """A tracker over the sim that swaps in oriented objects, the way a vision tracker would feed measurements to `Tools`."""
    def __init__(self, sim, overrides=None):
        self.sim, self.overrides = sim, overrides or {}

    def objects(self):
        return [self.overrides.get(o.id, o) for o in self.sim.objects()]

    def hands(self):
        return self.sim.hands()


def tools_with(sim, overrides=None):
    return Tools(WorldState(), Overlay(sim, overrides), sim, Safety(), say=lambda _: None)


def pen_with_yaw(sim, grasp_yaw):
    from dataclasses import replace
    pen = next(o for o in sim.objects() if o.id == 'obj3')
    return {'obj3': replace(pen, position=(*pen.position[:2], GRASP_Z), grasp_yaw_deg=grasp_yaw)}


@pytest.fixture
def sim():
    s = Simulation(realtime=False, pen=True)
    yield s
    s.close()


@pytest.mark.parametrize('pen_yaw', [0, 30, 75, 120, 170])
def test_pick_succeeds_with_the_jaws_across_the_pen(sim, pen_yaw):
    sim.place_pen(*PEN_AT, pen_yaw)
    result = tools_with(sim, pen_with_yaw(sim, pen_yaw + 90)).pick('obj3')
    assert result == 'picked pen' and sim.held == 'obj3', sim.reason


@pytest.mark.parametrize('error', [-60, 90])        # 90 degrees off = jaws along the pen
def test_pick_misses_when_the_jaws_are_off(sim, error):
    sim.place_pen(*PEN_AT, 30)
    result = tools_with(sim, pen_with_yaw(sim, 30 + 90 + error)).pick('obj3')
    assert result.startswith('blocked') and sim.held is None
    assert 'close misses' in sim.reason


def test_tolerance_edge(sim):
    sim.place_pen(*PEN_AT, 0)
    assert tools_with(sim, pen_with_yaw(sim, 90 + 25)).pick('obj3') == 'picked pen'
    sim.reset(); sim.place_pen(*PEN_AT, 0)
    assert tools_with(sim, pen_with_yaw(sim, 90 + 35)).pick('obj3').startswith('blocked')


def test_a_pen_picked_without_a_yaw_fails_but_a_cube_does_not(sim):
    sim.place_pen(*PEN_AT, 45)
    tools = tools_with(sim, pen_with_yaw(sim, None))                # at the sim's grasp height, but with no measured yaw
    assert tools.pick('obj3').startswith('blocked') and 'no grasp yaw' in sim.reason
    assert tools.pick('obj1') == 'picked red cube'


def test_default_scene_has_no_pen():
    s = Simulation(realtime=False)
    try:
        assert [o.id for o in s.objects()] == ['obj1', 'obj2']
        with pytest.raises(ValueError):
            s.place_pen(0.2, 0, 0)
    finally:
        s.close()


def test_bad_yaw_is_rejected(sim):
    for bad in (float('nan'), 'x', True):
        with pytest.raises(ValueError):
            sim.set_grasp_yaw(bad)


# ---- end to end: sim-rendered masks -> calibration from the rendered marker -> geometry -> pick -----------------------------------

@pytest.fixture(scope='module')
def rendered():
    s = Simulation(realtime=False, pen=True)
    cameras = Cameras(s, width=1280, height=720)
    cameras.ready.wait(30)
    assert cameras.error is None, cameras.error
    yield s, cameras
    cameras.close(); s.close()


def fresh_frames(cameras, since):
    """Frames certainly rendered after `since`. The camera thread copies the sim state at the start of a loop and stamps frames at the
    end, so a frame newer than `since` can still show the old scene. Wait for a pair, then for a pair stamped after the later of those two:
    a loop only starts after the previous one ends, so the second pair was rendered from state copied after `since`."""
    def newer(than):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            frames = [cameras.frame(n) for n in ('camera_a', 'camera_b')]
            stamps = [f['metadata']['timestamp'] for f in frames]
            if min(stamps) > than:
                return frames, max(stamps)
            time.sleep(0.05)
        raise AssertionError('no fresh frames')
    _, stamp = newer(since)
    frames, _ = newer(stamp)
    return frames


def views_from_render(sim, cameras, yaw):
    sim.reset()
    sim.place_pen(*PEN_AT, yaw)
    frames = fresh_frames(cameras, time.monotonic())
    geom = sim.model.geom('obj3_geom').id
    views = []
    for f in frames:
        meta = f['metadata']
        corners = detect_marker(f['rgb'][:, :, ::-1])
        assert corners is not None
        cal = TableCalibration.from_marker(corners, 0.12, meta['K'], meta['dist'])
        seg = f['segmentation']
        views.append(View(cal, (seg[:, :, 0] == geom) & (seg[:, :, 1] == 5)))        # mjOBJ_GEOM = 5
    return views


@pytest.mark.parametrize('yaw', [0, 35, 70, 110, 150])
def test_rendered_masks_to_a_successful_pick(rendered, yaw):
    sim, cameras = rendered
    views = views_from_render(sim, cameras, yaw)
    rough = next(o for o in sim.objects() if o.id == 'obj3')
    oriented = oriented_object(rough, views, jaw_opening_m=0.05, grasp_z_m=GRASP_Z)
    error = abs((oriented.grasp_yaw_deg - (yaw + 90.0) + 90.0) % 180.0 - 90.0)
    assert error < 5.0, (yaw, oriented.grasp_yaw_deg)
    assert math.dist(oriented.position[:2], PEN_AT) < 0.01
    assert tools_with(sim, {'obj3': oriented}).pick('obj3') == 'picked pen', sim.reason


def test_rendered_masks_with_the_wrong_object_are_refused(rendered):
    sim, cameras = rendered
    views = views_from_render(sim, cameras, 40)
    other = View(views[1].calibration, np.zeros_like(views[1].mask))
    with pytest.raises(GeometryError):
        oriented_object(next(o for o in sim.objects() if o.id == 'obj3'), [views[0], other])
