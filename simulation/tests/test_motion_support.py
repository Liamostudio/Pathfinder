"""Rest and deterministic Task 3 hooks, including the real HTTP bridge."""
import json
import math
import threading
import time
from http.server import ThreadingHTTPServer
import numpy as np
import pytest
from sim_env.engine import Simulation
from sim_env.server import Session,handler_for
from sim_env.client import SimulationClient
from sim_env.model import JOINTS
from sim_env.rest_pose import REST_MARGIN_DEG
from lookandpick.adapters.so101_profile import REST_READING,TOUCH_CALIBRATION
from lookandpick.workspace_bridge import urdf_joint_radians

@pytest.fixture
def sim():
    s=Simulation(realtime=False)
    try:yield s
    finally:s.close()

def wait_for(predicate,seconds=2):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Simulation did not reach the expected state')

def test_rest_maps_recorded_joints_backs_off_stops_and_keeps_scene_and_gripper(sim):
    before={o.id:o.position for o in sim.objects()};aperture=sim.data.qpos[sim.qids[5]]
    reading=dict(REST_READING);reading['shoulder_lift']+=REST_MARGIN_DEG;reading['elbow_flex']-=REST_MARGIN_DEG
    mapped=urdf_joint_radians(reading,json.loads(TOUCH_CALIBRATION.read_text())['offsets_deg'])
    margin=math.radians(REST_MARGIN_DEG)
    expected=np.clip([mapped[n] for n in JOINTS[:5]],sim.limits[:5,0]+margin,sim.limits[:5,1]-margin)
    assert np.allclose(sim.rest_targets[:5],expected)
    assert sim.rest() and np.allclose(sim.data.qpos[sim.qids[:5]],expected,atol=.003)
    assert sim.data.qpos[sim.qids[5]]==aperture
    assert np.linalg.norm(np.array(sim.position)-sim.rest_position)<.003
    assert all(np.linalg.norm(np.array(o.position)-before[o.id])<.002 for o in sim.objects())
    assert not sim.halted and sim.reason=='ready'

def test_rest_is_refused_while_stopped_and_does_not_reset_objects(sim):
    assert sim.nudge('obj1',.03,0);before=sim.objects()[0].position
    sim.halt();assert not sim.rest() and sim.safety.stopped
    assert sim.objects()[0].position==before

def test_rest_pauses_then_completes_and_cancel_can_interrupt_it(sim):
    sim.set_speed_scale(0);result=[]
    worker=threading.Thread(target=lambda:result.append(sim.rest()));worker.start()
    wait_for(lambda:sim.target is not None)
    # mj_step briefly integrates gravity before the kinematic hold restores qpos.
    # Readers must take the same lock as state(), not sample that intermediate state.
    with sim.lock:initial=sim.data.qpos[sim.qids].copy()
    time.sleep(.03)
    with sim.lock:paused=sim.data.qpos[sim.qids].copy()
    assert worker.is_alive() and np.array_equal(initial,paused)
    sim.set_speed_scale(1);worker.join(3);assert result==[True]
    sim.reset();sim.set_speed_scale(0);result.clear()
    worker=threading.Thread(target=lambda:result.append(sim.rest()));worker.start();wait_for(lambda:sim.target is not None)
    sim.cancel();worker.join(2);assert result==[False] and not sim.halted and not sim.safety.stopped

def test_rest_waits_for_the_same_full_arm_hand_check(sim):
    sim.set_hand(sim.position);result=[]
    worker=threading.Thread(target=lambda:result.append(sim.rest()));worker.start();wait_for(lambda:sim.target is not None)
    time.sleep(.03);assert worker.is_alive()
    sim.set_hand(None);worker.join(3);assert result==[True]

def test_nudge_during_a_move_does_not_cancel_the_move_or_nudge_a_held_object(sim):
    sim.set_speed_scale(0);result=[];target=sim.data.qpos[sim.qids].copy();target[0]+=.1
    worker=threading.Thread(target=lambda:result.append(sim.move_joints(target)));worker.start();wait_for(lambda:sim.target is not None)
    before=sim.objects()[0].position;assert sim.nudge('obj1',.03,-.02)
    after=sim.objects()[0].position;assert np.allclose(np.array(after)-before,[.03,-.02,0])
    assert worker.is_alive() and sim.target is not None
    sim.held='obj1';assert sim.nudge('obj1',.03,0) is False;assert sim.objects()[0].position==after
    sim.held=None;sim.set_speed_scale(1);worker.join(2);assert result==[True]

def test_one_missed_close_clears_the_fault_then_a_real_close_attaches(sim):
    target=next(o for o in sim.objects() if o.id=='obj1').position
    assert sim.move_to(target,1)
    assert sim.grip_miss_once();assert sim.grip(False)
    assert 'grip_miss_once' in sim.faults
    assert sim.grip(True) is False and sim.held is None
    assert 'grip_miss_once' not in sim.faults
    assert sim.grip(False) and sim.grip(True) and sim.held=='obj1'
    aperture=sim.data.qpos[sim.qids[5]]
    sim.rest() # It may refuse an obstructed Rest; it must never release the held cube.
    assert sim.held=='obj1' and sim.data.qpos[sim.qids[5]]==aperture

def test_hide_only_changes_tracking_and_render_groups_then_reset_clears_hooks(sim):
    before=sim.data.qpos.copy();contacts=sim.model.geom_contype.copy()
    assert sim.hide('obj1') and 'obj1' not in {o.id for o in sim.objects()}
    assert all(sim.model.geom_group[i]==5 for i in sim.object_geoms['obj1'])
    assert np.array_equal(before,sim.data.qpos) and np.array_equal(contacts,sim.model.geom_contype)
    sim.grip_miss_once();sim.reset()
    assert 'obj1' in {o.id for o in sim.objects()} and not sim.faults
    assert np.array_equal(sim.model.geom_group,sim.original_geom_groups)

def test_invalid_hooks_leave_the_scene_untouched(sim):
    before=sim.data.qpos.copy()
    for action in (lambda:sim.nudge('unknown',.03,0),lambda:sim.nudge('obj1',math.nan,0),lambda:sim.nudge('obj1',True,0),lambda:sim.nudge('obj1',.2,0),lambda:sim.hide('obj1','yes')):
        with pytest.raises(ValueError):action()
    assert np.array_equal(before,sim.data.qpos)

def test_hidden_object_disappears_from_real_camera_segmentation_and_can_return(sim):
    from sim_env.cameras import Cameras
    cameras=Cameras(sim,width=320,height=180,fps=20)
    try:
        assert cameras.ready.wait(10),cameras.error
        def fresh():
            try:return cameras.frame('camera_a')
            except RuntimeError:return None
        frame=fresh();assert frame is not None
        ids=sim.object_geoms['obj1']
        assert np.isin(frame['segmentation'][:,:,0],ids).any()
        sim.hide('obj1');wait_for(lambda:fresh() is not None)
        hidden=fresh();assert not np.isin(hidden['segmentation'][:,:,0],ids).any()
        assert hidden['metadata']['scene_revision']==sim.scene_revision
        sim.hide('obj1',False);wait_for(lambda:fresh() is not None)
        assert np.isin(fresh()['segmentation'][:,:,0],ids).any()
    finally:cameras.close()

def test_http_hooks_bypass_a_running_move_and_rest_uses_a_job():
    session=Session(render=False,realtime=False);server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(session))
    service=threading.Thread(target=server.serve_forever,daemon=True);service.start();client=SimulationClient(f'http://127.0.0.1:{server.server_port}')
    try:
        assert client.reachable((.2,.1,.1)) and not client.reachable((9,9,9))
        client.set_speed_scale(0);result=[]
        target=[v['position'] for v in client.state()['joints'].values()];target[0]+=.1
        worker=threading.Thread(target=lambda:result.append(client.action('joints',positions=target)));worker.start();wait_for(lambda:session.sim.target is not None)
        assert client.nudge('obj1',.03,0) and client.hide('obj2') and client.grip_miss_once()
        assert 'obj2' not in {o.id for o in client.objects()} and worker.is_alive()
        client.cancel();worker.join(2);assert result[0]['status']=='refused' and not client.state()['stopped']
        client.set_speed_scale(1);assert client.rest()
        assert np.linalg.norm(np.array(client.position)-client.rest_position)<.003
        client.halt();assert not client.rest()
    finally:session.sim.cancel();server.shutdown();server.server_close();service.join(2);session.close()
