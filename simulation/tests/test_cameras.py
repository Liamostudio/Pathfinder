import time
import cv2
import numpy as np
import pytest
from lookandpick.calibration import detect_marker,TableCalibration
from lookandpick.intrinsics import Intrinsics
from lookandpick.adapters.camera import Recorder,ReplayCamera
from lookandpick.world import Hand
from sim_env.engine import Simulation
from sim_env.cameras import Cameras
from sim_env.hands import GroundTruthHandDetector
from sim_env.faults import CameraFaults,FaultCamera,FaultDetector


@pytest.fixture(scope='module')
def rendered():
    sim=Simulation(realtime=False)
    cameras=Cameras(sim,width=1280,height=720)
    cameras.ready.wait(30)
    assert cameras.error is None, cameras.error
    yield sim,cameras
    cameras.close();sim.close()


@pytest.mark.parametrize('name',['camera_a','camera_b'])
def test_real_calibration_from_render_within_one_cm(rendered,name):
    sim,cameras=rendered
    f=cameras.frame(name);meta=f['metadata']
    corners=detect_marker(f['rgb'][:,:,::-1])
    assert corners is not None
    cal=TableCalibration.from_marker(corners,.12,meta['K'],meta['dist'])
    pose=np.array(meta['world_from_camera'])
    assert np.linalg.norm(cal.camera_position()-pose[:3,3])<.01
    for point in [(0,0,0),(.20,.1,0),(.10,-.1,0)]:
        camera=np.linalg.inv(pose) @ [*point,1]
        pixel=np.array(meta['K']) @ camera[:3];pixel=pixel[:2]/pixel[2]
        assert np.linalg.norm(cal.pixel_to_table(pixel)-point[:2])<.01


def test_ground_truth_hand_detector_and_recorder(rendered,tmp_path):
    _,cameras=rendered
    frame=cameras.frame('camera_a');meta=frame['metadata']
    detector=GroundTruthHandDetector(lambda:[Hand('test',(.2,0,.1))],lambda:meta)
    result=detector.detect(frame['rgb'],0)
    assert len(result)==1 and len(result[0].pixels)==21
    assert np.isfinite(result[0].pixels).all()
    recorder=Recorder(tmp_path,'sim',fps=10)
    try:
        for i in range(3): recorder.write(i/10,frame['rgb'][:,:,::-1])
    finally: recorder.close()
    frames=list(ReplayCamera(recorder.path).frames())
    assert len(frames)==3
    assert frames[0][1].shape==(720,1280,3)


def test_lens_distortion_round_trip(tmp_path):
    sim=Simulation(realtime=False)
    k=np.array([[1060.,0,640],[0,1050.,360],[0,0,1]])
    dist=np.array([-.1,.01,0,0,0])
    Intrinsics(k,dist,(1280,720),0).save(tmp_path/'camera_b.json')
    cameras=Cameras(sim,width=1280,height=720,calibration_dir=tmp_path)
    try:
        cameras.ready.wait(30)
        assert cameras.error is None
        f=cameras.frame('camera_b')
        assert np.array_equal(f['metadata']['K'],k)
        assert np.array_equal(f['metadata']['dist'],dist)
        corners=detect_marker(f['rgb'][:,:,::-1])
        assert corners is not None
        cal=TableCalibration.from_marker(corners,.12,k,dist)
        pose=np.array(f['metadata']['world_from_camera'])
        assert np.linalg.norm(cal.camera_position()-pose[:3,3])<.01
    finally: cameras.close();sim.close()


class Frames:
    name='synthetic'
    def frames(self):
        for i in range(5): yield i/10,np.full((16,16,3),100,np.uint8)


def test_seeded_camera_faults():
    plan=CameraFaults(seed=10,noise_sigma=3,freeze_after=2)
    a=list(FaultCamera(Frames(),plan).frames())
    b=list(FaultCamera(Frames(),plan).frames())
    assert [x[0] for x in a]==[0,.1,.1,.1,.1]
    assert all(np.array_equal(x[1],y[1]) for x,y in zip(a,b))
    black=list(FaultCamera(Frames(),CameraFaults(black_after=0)).frames())
    assert not black[0][1].any()
