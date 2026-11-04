"""Launcher preset: prop-only, atomic, bounded, never clears the stop latch."""
import numpy as np
import pytest
from sim_env.engine import Simulation
from sim_env.server import Session

@pytest.fixture
def sim():
    value=Simulation(realtime=False)
    yield value
    value.close()


def test_preset_keeps_rest_joints_and_stop_state_and_moves_only_props(sim):
    assert sim.rest()
    joints=sim.data.qpos[sim.qids].copy();position=sim.position
    preset={'obj1':[.24,-.12],'obj2':[.16,-.20]}
    assert sim.scene_preset(preset)
    np.testing.assert_array_equal(sim.data.qpos[sim.qids],joints)
    assert sim.position==position
    objects={o.id:o.position for o in sim.objects()}
    for key,xy in preset.items():np.testing.assert_allclose(objects[key][:2],xy)
    sim.halt()
    with pytest.raises(ValueError,match='Reset Deck'):sim.scene_preset(preset)
    assert sim.halted


def test_invalid_preset_is_atomic_and_active_actions_refuse_it(sim):
    before=sim.data.qpos.copy()
    for positions in ({'obj1':[.2,0.]},{'obj1':[.2,0.],'obj2':[99,0.]},{'obj1':[.2,0.],'obj2':[.2,0.]},{'obj1':[True,0.],'obj2':[.16,-.2]}):
        with pytest.raises(ValueError):sim.scene_preset(positions)
        np.testing.assert_array_equal(sim.data.qpos,before)
    sim.command_lock.acquire()
    try:
        with pytest.raises(ValueError,match='idle'):sim.scene_preset({'obj1':[.24,-.12],'obj2':[.16,-.20]})
    finally:sim.command_lock.release()


def test_preset_is_available_through_real_http_client():
    import threading
    from http.server import ThreadingHTTPServer
    from sim_env.server import handler_for
    from sim_env.client import SimulationClient
    session=Session(render=False,realtime=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(session))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        client=SimulationClient(f'http://127.0.0.1:{server.server_port}')
        assert client.scene_preset({'obj1':[.24,-.12],'obj2':[.16,-.20]})['result'] is True
        objects={o['id']:o['position'] for o in client.state()['scene']['objects']}
        np.testing.assert_allclose(objects['obj1'][:2],[.24,-.12])
    finally:
        server.shutdown();server.server_close();thread.join(2);session.close()
