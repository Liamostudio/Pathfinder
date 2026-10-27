from dataclasses import replace
import threading
import time

import pytest

from lookandpick.adapters.base import PixelHand
from lookandpick.adapters.fake import SimArm
from lookandpick.adapters.hands import FakeHandDetector, project_hands
from lookandpick.hand_safety import HandDetectionLoop, HandSafetyMonitor
from lookandpick.safety import Safety
from lookandpick.world import Hand, WorldState


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Projector:
    def pixel_to_table(self, pixel, z=0):
        return pixel[0] / 1000, pixel[1] / 1000


def setup(timeout=.15):
    clock = Clock()
    arm, world, safety = SimArm(), WorldState(), Safety()
    monitor = HandSafetyMonitor(world, safety, arm, lambda: ((0, 0, 0), (1, 0, 0)),
                                timeout_s=timeout, clock=clock)
    return clock, arm, world, safety, monitor


def test_startup_requires_verified_observation_and_healthy_empty_is_clear():
    clock, arm, _, _, monitor = setup()
    assert monitor.tick() == arm.speed_scale == 0
    monitor.publish([], clock())
    assert monitor.tick() == arm.speed_scale == 1


def test_hand_approaches_stops_then_leaves_without_latching():
    clock, arm, world, safety, monitor = setup()
    for y, expected in [(.6, 1), (.3, None), (.1, 0), (.6, 1)]:
        clock.now += .03
        monitor.publish([Hand('h', (.5, y, 0))], clock())
        if expected is None:
            assert 0 < arm.speed_scale < 1
        else:
            assert arm.speed_scale == expected
    assert not safety.stopped
    assert world.snapshot()['hands'][0]['position'] == (.5, .6, 0)


def test_watchdog_stops_at_deadline_and_late_result_cannot_resume():
    clock, arm, _, safety, monitor = setup()
    monitor.publish([], clock())
    clock.now += .151
    assert monitor.tick() == 0
    assert arm.halted and safety.stopped
    monitor.publish([], clock())
    assert monitor.tick() == arm.speed_scale == 0
    safety.reset()
    assert monitor.tick() == 0 and safety.stopped


def test_watchdog_also_covers_startup_with_no_frames():
    clock, arm, _, safety, monitor = setup()
    clock.now += .151
    monitor.tick()
    assert safety.stopped and arm.halted


def test_manual_stop_bypasses_path_provider_and_never_resumes():
    clock, arm, _, safety, monitor = setup()
    monitor.path = lambda: pytest.fail('Path should not be read after stop')
    safety.stop()
    assert monitor.tick() == 0 and arm.halted
    monitor.publish([], clock())
    assert arm.speed_scale == 0


@pytest.mark.parametrize('stamp', [float('nan'), float('inf'), 100.01])
def test_invalid_timestamp_fails_closed(stamp):
    _, arm, _, safety, monitor = setup()
    monitor.publish([], stamp)
    assert safety.stopped and arm.halted


def test_repeated_observation_timestamp_is_a_fault():
    clock, _, _, safety, monitor = setup()
    monitor.publish([], clock())
    monitor.publish([], clock())
    assert safety.stopped


def test_invalid_hand_data_and_path_errors_latch_stop():
    clock, arm, _, safety, monitor = setup()
    monitor.publish([Hand('h', (float('nan'), 0, 0))], clock())
    assert safety.stopped and arm.halted
    clock, arm, _, safety, monitor = setup()
    monitor.path = lambda: 1 / 0
    monitor.publish([], clock())
    assert safety.stopped and arm.halted


def test_projected_envelope_covers_fingers_and_uncertainty():
    hand = project_hands([PixelHand('h', ((400, 100), (600, 100)))], Projector(),
                         hand_height=.08, uncertainty_m=.03)[0]
    assert hand.position == pytest.approx((.5, .1, .08))
    assert hand.radius_m == pytest.approx(.13)
    safety = Safety()
    assert safety.allowed_speed([hand], ((0, 0, 0), (1, 0, 0))) == 0


def test_marker_to_arm_offset_can_be_applied_explicitly():
    hand=project_hands([PixelHand('h',((100,200),))],Projector(),hand_height=.08,
                       table_to_arm=lambda p:(p[0]+.05,p[1]-.02,p[2]+.01))[0]
    assert hand.position==pytest.approx((.15,.18,.09))


def test_projection_uses_table_calibration_with_height():
    np = pytest.importorskip('numpy')
    pytest.importorskip('cv2')
    from lookandpick.calibration import TableCalibration
    # Camera looking down, one metre above table.
    calibration = TableCalibration(np.array([[500.,0,320],[0,500,240],[0,0,1]]),
                                   np.array([3.141592653589793,0,0]), np.array([0.,0.,1.]))
    pixel = calibration.table_to_pixel((.2, .1), z=.08)
    hand = project_hands([PixelHand('h', (tuple(pixel),))], calibration, hand_height=.08)[0]
    assert hand.position == pytest.approx((.2,.1,.08))


def test_independent_watchdog_stops_while_detector_is_blocked():
    entered, release = threading.Event(), threading.Event()

    class Source:
        def frames(self):
            yield 0., object()

    class SlowDetector(FakeHandDetector):
        def detect(self, frame, timestamp_ms):
            entered.set()
            release.wait(2)
            return []

    arm, safety = SimArm(), Safety()
    monitor = HandSafetyMonitor(WorldState(), safety, arm, lambda: ((0,0,0), (1,0,0)),
                                timeout_s=.05, watchdog_hz=200)
    loop = HandDetectionLoop(Source(), SlowDetector(), Projector(), monitor, hand_height=.08)
    started = time.monotonic()
    try:
        loop.start()
        assert entered.wait(.3)
        assert safety._stopped.wait(.3)
        # A generous CI ceiling; exact deadline is tested with the deterministic clock above.
        assert time.monotonic() - started < .35
        assert arm.halted
    finally:
        release.set()
        loop.close()


def test_replay_camera_frames_feed_monitor_and_end_fails_closed(tmp_path):
    np = pytest.importorskip('numpy')
    pytest.importorskip('cv2')
    from lookandpick.adapters.camera import Recorder, ReplayCamera
    recorder = Recorder(tmp_path, 'hands')
    for i in range(3):
        recorder.write(i / 30, np.zeros((48,64,3), dtype=np.uint8))
    recorder.close()
    arm, safety = SimArm(), Safety()
    monitor = HandSafetyMonitor(WorldState(), safety, arm, lambda: ((0,0,0), (1,0,0)))
    loop = HandDetectionLoop(ReplayCamera(recorder.path), FakeHandDetector(), Projector(),
                             monitor, hand_height=.08)
    try:
        loop.start()
        assert safety._stopped.wait(1)
        assert loop.frames_processed == 3
        assert monitor.reason == 'frame source ended'
        assert arm.halted
    finally:
        loop.close()


def test_tool_refresh_preserves_monitored_hands():
    from lookandpick.adapters.fake import FakeTracker
    from lookandpick.hand_safety import MonitoredTracker
    from lookandpick.tools import Tools
    clock, arm, world, safety, monitor = setup()
    monitor.publish([Hand('h',(.25,.1,.02))],clock())
    tools = Tools(world,MonitoredTracker(FakeTracker(),monitor),arm,safety)
    assert tools.get_scene()['hands']
    assert tools.pick('obj1').startswith('blocked')
    assert not arm.gripping


def test_frame_timestamp_stall_and_detector_exception_latch_stop():
    class Source:
        def frames(self):
            yield 0,object()
            yield 0,object()

    class BrokenDetector(FakeHandDetector):
        def detect(self,*args):
            raise RuntimeError('synthetic failure')

    for detector in (FakeHandDetector(),BrokenDetector()):
        arm,safety=SimArm(),Safety()
        monitor=HandSafetyMonitor(WorldState(),safety,arm,lambda:((0,0,0),(1,0,0)))
        loop=HandDetectionLoop(Source(),detector,Projector(),monitor,hand_height=.08)
        try:
            loop.start()
            assert safety._stopped.wait(.5)
            assert arm.halted
            assert 'error' in monitor.reason
        finally:
            loop.close()


def test_old_clear_decision_cannot_overwrite_new_block():
    clock,arm,_,_,monitor=setup()
    monitor.publish([],clock())
    entered,release=threading.Event(),threading.Event()
    setter=arm.set_speed_scale
    block_once=[True]

    def delayed_setter(scale):
        if scale==1 and block_once[0]:
            block_once[0]=False
            entered.set()
            assert release.wait(1)
        setter(scale)

    arm.set_speed_scale=delayed_setter
    old=threading.Thread(target=monitor.tick)
    old.start()
    assert entered.wait(1)
    clock.now+=.01
    new=threading.Thread(target=lambda:monitor.publish([Hand('h',(.5,.01,0))],clock()))
    new.start()
    release.set()
    old.join(1)
    new.join(1)
    assert not old.is_alive() and not new.is_alive()
    assert arm.speed_scale==monitor.scale==0


def test_result_expiring_during_evaluation_does_not_reenable_motion():
    clock,arm,_,safety,monitor=setup()

    def slow_path():
        clock.now+=.2
        return ((0,0,0),(1,0,0))

    monitor.path=slow_path
    monitor.publish([],clock())
    assert safety.stopped and arm.speed_scale==0
