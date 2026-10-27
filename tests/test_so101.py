from dataclasses import replace
from types import SimpleNamespace

import pytest

from lookandpick.adapters.so101 import JOINTS, LeRobotFollowerBus, SO101Arm, SO101Limits, require_local_presence
from lookandpick.adapters.so101_health import ReadOnlySO101Health


class Kinematics:
    """Test-only mapping, explicitly NOT SO-101 kinematics."""
    def inverse(self, target, seed):
        return {**seed,'shoulder_pan':target[0]*100,'shoulder_lift':target[1]*100,'elbow_flex':(target[2]-.2)*100}

    def forward(self, joints):
        return (joints['shoulder_pan']/100, joints['shoulder_lift']/100, .2+joints['elbow_flex']/100)


class FakeBus:
    def __init__(self):
        self.positions = {name:0. for name in JOINTS}
        self.writes = []
        self.halted = False

    def read_positions(self):
        return dict(self.positions)

    def send_positions(self, joints):
        self.writes.append(dict(joints))
        self.positions = dict(joints)

    def set_gripper_torque_limit(self, raw):
        self.writes.append(('torque_limit',raw))

    def halt(self):
        self.halted = True


def test_dry_run_never_sends_or_halts_bus():
    bus = FakeBus()
    arm = SO101Arm(bus=bus,kinematics=Kinematics())
    assert arm.move_to((.1,.05,.22),.5)
    assert arm.grip(False)
    arm.halt()
    assert arm.log and not bus.writes and not bus.halted
    assert not arm.move_to((0,0,.2),1)


def test_limits_clamp_and_velocity_and_step_are_bounded():
    arm = SO101Arm(kinematics=Kinematics())
    goal = {name:999. for name in JOINTS}
    step = arm.preview_step(goal, speed_scale=.5)
    assert all(0 <= value <= .25 for value in step.values())
    # Wrist roll does not affect test FK, so we can check its absolute clamp too.
    goal = dict(arm.joints, wrist_roll=999.)
    assert not arm.move_joints(goal)
    assert arm.joints['wrist_roll'] == 60
    entries = [j for kind,j in arm.log if kind == 'dry_run']
    previous = {name:0. for name in JOINTS}
    for entry in entries:
        assert all(abs(entry[k]-previous[k]) <= .5+1e-9 for k in JOINTS)
        previous = entry


def test_workspace_and_missing_kinematics_refuse_without_motion():
    arm = SO101Arm(kinematics=Kinematics())
    assert not arm.move_to((.31,0,.2),1)
    assert not arm.move_to((float('nan'),0,.2),1)
    assert not SO101Arm().move_to((.1,0,.2),1)
    assert not arm.log or all(kind.startswith('refused') for kind,_ in arm.log)


def test_invalid_observed_pose_and_config_are_rejected():
    with pytest.raises(ValueError,match='outside'):
        SO101Arm(initial_joints={**{k:0. for k in JOINTS},'wrist_roll':1000.})
    with pytest.raises(ValueError): SO101Limits(max_velocity=100)
    with pytest.raises(ValueError): SO101Limits(gripper_torque_raw=1000)
    limits = SO101Limits()
    with pytest.raises(TypeError): limits.joint_ranges['gripper'] = (0,1000)


@pytest.mark.parametrize('present,tty,remote', [(False,True,False),(True,False,False),(True,True,True)])
def test_presence_guard_refuses_unattended_or_remote(present,tty,remote):
    stream = SimpleNamespace(isatty=lambda:tty)
    with pytest.raises(PermissionError):
        require_local_presence(present,stdin=stream,stdout=stream,
                               environ={'SSH_CONNECTION':'remote'} if remote else {},
                               confirm=lambda _:pytest.fail('Must refuse before prompting'))


def test_presence_requires_exact_confirmation_and_commissioning():
    stream = SimpleNamespace(isatty=lambda:True)
    with pytest.raises(PermissionError):
        require_local_presence(True,stdin=stream,stdout=stream,environ={},confirm=lambda _:'yes')
    require_local_presence(True,stdin=stream,stdout=stream,environ={},confirm=lambda _:'6V KILL SWITCH READY')
    with pytest.raises(PermissionError,match='commissioned'):
        SO101Arm(dry_run=False,i_am_present=True,presence_check=lambda _:None)


def test_fake_live_bus_stop_bypasses_action_lock_and_cannot_resume():
    bus = FakeBus()
    arm = SO101Arm(bus=bus,kinematics=Kinematics(),dry_run=False,i_am_present=True,
                   limits=replace(SO101Limits(),commissioned=True),presence_check=lambda _:None)
    original_send = bus.send_positions

    def send_then_stop(joints):
        original_send(joints)
        arm.halt()

    bus.send_positions = send_then_stop
    assert not arm.move_to((.1,0,.2),1)
    assert bus.halted
    writes = list(bus.writes)
    assert not arm.grip(False)
    assert not arm.move_to((0,0,.2),1)
    arm.set_speed_scale(1)
    assert arm._scale == 0 and bus.writes == writes


def test_follower_wrapper_does_not_connect_and_ack_is_not_completion():
    calls = []
    follower = SimpleNamespace(is_connected=True,is_calibrated=True,config=SimpleNamespace(use_degrees=True),
        send_action=lambda action:calls.append(action),
        bus=SimpleNamespace(sync_read=lambda _:{k:0. for k in JOINTS},
                            write=lambda *args:calls.append(args),disable_torque=lambda:calls.append('halt')))
    bus = LeRobotFollowerBus(follower)
    assert bus.read_positions() == {name:0. for name in JOINTS}
    bus.send_positions({name:1. for name in JOINTS})
    assert calls[0]['shoulder_pan.pos'] == 1


def test_submitted_but_unreached_target_is_not_success():
    bus=FakeBus()
    bus.send_positions=lambda joints:bus.writes.append(dict(joints))
    arm=SO101Arm(bus=bus,kinematics=Kinematics(),dry_run=False,i_am_present=True,
                 limits=replace(SO101Limits(),commissioned=True),presence_check=lambda _:None)
    target=dict(arm.joints,shoulder_pan=1.)
    assert not arm.move_joints(target,timeout_s=.01)
    assert bus.halted


def test_health_reader_calls_only_open_read_close_without_torque_write():
    calls=[]

    class RawBus:
        def connect(self,**kw): calls.append(('connect',kw))
        def disconnect(self,**kw): calls.append(('disconnect',kw))
        def read(self,register,name,**kw):
            calls.append(('read',register,name,kw))
            return JOINTS.index(name)+1 if register=='ID' else 60 if register=='Present_Voltage' else 25
        def write(self,*args): pytest.fail('Health check must never write')

    reader=ReadOnlySO101Health('/dev/fake',bus_factory=lambda _:RawBus())
    reader.open()
    rows=reader.read()
    reader.close()
    assert len(rows)==6 and all(r['voltage_v']==6 and not r['errors'] for r in rows)
    assert calls[0]==('connect',{'handshake':False})
    assert calls[-1]==('disconnect',{'disable_torque':False})
