import math
import threading
import time
import numpy as np
import pytest
from lookandpick.adapters.fake import SimArm,FakeTracker
from lookandpick.safety import Safety
from lookandpick.tools import Tools
from lookandpick.world import WorldState
from sim_env.engine import Simulation


@pytest.fixture(params=['fake','mujoco'])
def arm(request):
    obj=SimArm() if request.param=='fake' else Simulation(realtime=False)
    yield obj
    if hasattr(obj,'close'): obj.close()


def test_arm_completion_and_stop_contract(arm):
    assert arm.move_to((.23,.10,.15),.5) is True
    assert math.dist(arm.position,(.23,.10,.15))<.004
    arm.halt()
    frozen=arm.position
    assert arm.move_to((.2,0,.2),1) is False
    assert arm.grip(True) is False
    arm.set_speed_scale(1)
    assert arm.move_to((.2,0,.2),1) is False
    assert arm.position==frozen


def test_invalid_speed_contract(arm):
    for scale in (-1,1.01,float('nan')):
        with pytest.raises(ValueError): arm.set_speed_scale(scale)


@pytest.mark.parametrize('kind',['fake','mujoco'])
def test_tracker_schema(kind):
    tracker=FakeTracker() if kind=='fake' else Simulation(realtime=False)
    try:
        scene=Tools(WorldState(),tracker,SimArm(),Safety()).get_scene()
        assert set(scene)=={'objects','hands','arm_position'}
        assert len(scene['objects'])>=2
        assert all(len(o['position'])==3 for o in scene['objects'])
    finally:
        if hasattr(tracker,'close'): tracker.close()


def test_tools_clean_pick_place():
    sim=Simulation(realtime=False)
    try:
        assert sim.tools.pick('obj1')=='picked red cube'
        assert sim.held=='obj1'
        assert sim.tools.place('obj1',(.18,-.2,.018))=='placed red cube'
        assert sim.held is None
        for _ in range(50): sim.step()
        obj=next(o for o in sim.objects() if o.id=='obj1')
        assert math.dist(obj.position,(.18,-.2,.018))<.01
    finally: sim.close()


def test_unreachable_and_bad_targets_refuse_without_motion():
    sim=Simulation(realtime=False)
    try:
        before=sim.position
        for target in ((4,4,4),(float('nan'),0,.1),(0,0,-1)):
            assert sim.move_to(target,1) is False
            assert sim.position==before
    finally: sim.close()


def test_pause_resume_and_independent_halt():
    sim=Simulation()
    result=[]
    worker=threading.Thread(target=lambda:result.append(sim.move_to((.20,.08,.15),1)))
    try:
        worker.start()
        deadline=time.monotonic()+3
        while not sim.command_lock.locked() and time.monotonic()<deadline: time.sleep(.005)
        sim.set_speed_scale(0)
        frozen=sim.position
        time.sleep(.06)
        assert math.dist(frozen,sim.position)<1e-9
        sim.set_speed_scale(1)
        time.sleep(.08)
        assert math.dist(frozen,sim.position)>.001
        t=time.perf_counter(); sim.halt(); assert time.perf_counter()-t<.1
        frozen=sim.position
        worker.join(timeout=2)
        assert result==[False]
        time.sleep(.04)
        assert sim.position==frozen
        sim.set_speed_scale(1)
        assert sim.speed_scale==0
    finally:
        sim.close();worker.join(timeout=2)


def test_joint_limits_and_table_collision():
    sim=Simulation(realtime=False)
    try:
        with pytest.raises(ValueError): sim.move_joints([10]*6)
        q=sim.data.qpos[sim.qids].copy()
        # A large fold must be refused either by limits or swept collision checks.
        result=sim.move_joints([-.266875,1.008615,1.636437,-.432005,2.667698,1.60907])
        assert result is False
        assert 'intersects' in sim.reason or 'collision' in sim.reason
    finally: sim.close()
