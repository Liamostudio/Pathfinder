"""The 'workspace' layout: the arm base where the tip-touch calibration measured it, and both cubes on the real table, near the marker, clear of its plate."""
import json
import math

import pytest

from sim_env.engine import Simulation
from sim_env.model import layout


@pytest.fixture
def sim():
    s = Simulation(realtime=False, layout='workspace')
    yield s
    s.close()


def test_the_base_comes_from_the_calibration_file_and_the_default_layout_is_untouched():
    from lookandpick.adapters.so101_profile import TOUCH_CALIBRATION
    frame = json.loads(TOUCH_CALIBRATION.read_text())['base_frame']
    ws, default = layout('workspace'), layout('default')
    assert ws['base'][:2] == pytest.approx(frame['offset_m']) and ws['base_yaw_deg'] == pytest.approx(frame['yaw_deg'])
    assert default['base'] == (0.0, -0.12, 0.0) and default['base_yaw_deg'] == 0.0 and default['objects']['obj1'][1] == (0.23, 0.10, 0.018)


def test_both_cubes_are_near_the_marker_on_the_table_and_clear_of_its_plate():
    for name, (_, (x, y, z), _) in layout('workspace')['objects'].items():
        assert max(abs(x), abs(y)) > 0.08 + 0.018 + 0.02, f'{name} would sit on the marker plate'      # plate edge + cube half + margin
        assert math.hypot(x, y) < 0.20, f'{name} is not near the marker'
        assert -0.45 < x < 0.20 and -0.30 < y < 0.30, f'{name} is off the table'                      # the real table's extent in the marker frame
        assert z == 0.018


def test_both_cubes_are_reachable_and_a_pick_and_place_works(sim):
    for o in sim.objects():
        assert sim.reachable((o.position[0], o.position[1], 0.018)) and sim.reachable((o.position[0], o.position[1], 0.118))
    from lookandpick.safety import Safety
    from lookandpick.tools import Tools
    from lookandpick.world import WorldState
    tools = Tools(WorldState(), sim, sim, Safety.for_arm(sim), say=lambda _: None)
    assert tools.pick('obj1').startswith('picked')
    blue = next(o for o in sim.objects() if o.id == 'obj2').position
    assert tools.place('obj1', (blue[0] - 0.07, blue[1], 0.018)).startswith('placed')
    red = next(o for o in sim.objects() if o.id == 'obj1').position
    assert 0.04 < math.dist(red[:2], blue[:2]) < 0.10
