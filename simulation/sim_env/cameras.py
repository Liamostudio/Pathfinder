"""Offscreen 3D views. OpenGL resources stay on their owning render thread."""
from __future__ import annotations
import io
from pathlib import Path
import cv2
import math
import threading
import time
import mujoco
import numpy as np
from PIL import Image

PRESETS = {
    'orbit': {'azimuth': 135., 'elevation': -28., 'distance': 0.95, 'lookat': [0.16, 0, 0.10]},
    'camera_a': {'azimuth': -90., 'elevation': -45., 'distance': 0.65, 'lookat': [0.08, 0, 0.04]},
    'camera_b': {'azimuth': 0., 'elevation': -45., 'distance': 0.65, 'lookat': [0.08, 0, 0.04]},
    'top': {'azimuth': 90., 'elevation': -75., 'distance': 0.85, 'lookat': [0.16, 0, 0]},
    'wrist': {'azimuth': 90., 'elevation': -30., 'distance': 0.25, 'lookat': [0.2, 0, 0.1]},
}


class Cameras:
    def __init__(self, simulation, *, width=640, height=360, fps=10, calibration_dir=None):
        self.sim = simulation
        self.width, self.height, self.period = width, height, 1/fps
        self.intrinsics = {}
        self.maps = {}
        if calibration_dir:
            from lookandpick.intrinsics import Intrinsics
            for name in PRESETS:
                path = Path(calibration_dir) / (name + ".json")
                if path.exists():
                    intrinsic = Intrinsics.load(path)
                    if intrinsic.size != (width, height):
                        raise ValueError(f"{path}: intrinsics size must match render resolution")
                    self.intrinsics[name] = intrinsic
        self.lock = threading.Lock()
        self.poses = {n: {**p, 'lookat': list(p['lookat'])} for n, p in PRESETS.items()}
        self.frames = {}
        self.error = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='sim-render', daemon=True)
        self.thread.start()

    def configure(self, name, values):
        if name not in self.poses or name == 'wrist':
            raise ValueError('Choose orbit, camera_a, camera_b or top')
        if set(values) - {'azimuth', 'elevation', 'distance', 'lookat'}:
            raise ValueError('Unknown camera property')
        with self.lock:
            pose = {**self.poses[name], **values}
            if not all(isinstance(pose[k], (int, float)) and math.isfinite(pose[k]) for k in ('azimuth', 'elevation', 'distance')):
                raise ValueError('Camera angles and distance must be finite')
            if not (-89.9 <= pose['elevation'] <= -2 and 0.12 <= pose['distance'] <= 3):
                raise ValueError('Camera elevation [-89.9, -2] degrees; distance [0.12, 3] metres')
            if len(pose['lookat']) != 3 or not all(isinstance(x,(int,float)) and math.isfinite(x) and abs(x) <= 2 for x in pose['lookat']):
                raise ValueError('Invalid camera target')
            self.poses[name] = pose

    def snapshot(self):
        with self.lock:
            return {'poses': {n: dict(p) for n, p in self.poses.items()}, 'error': self.error,
                    'ready': bool(self.frames), 'width': self.width, 'height': self.height}

    def frame(self, name):
        with self.lock:
            if name not in PRESETS:
                raise ValueError('Unknown camera')
            if name not in self.frames:
                raise RuntimeError(self.error or 'Renderer starting')
            return self.frames[name]

    def _run(self):
        renderer = None
        try:
            renderer = mujoco.Renderer(self.sim.model, width=self.width, height=self.height)
            data = mujoco.MjData(self.sim.model)
            camera = mujoco.MjvCamera()
            mujoco.mjv_defaultCamera(camera)
            camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            while not self.stop.is_set():
                start = time.monotonic()
                with self.sim.lock:
                    data.qpos[:] = self.sim.data.qpos
                    data.mocap_pos[:] = self.sim.data.mocap_pos
                    data.mocap_quat[:] = self.sim.data.mocap_quat
                    data.time = self.sim.data.time
                    mujoco.mj_forward(self.sim.model, data)
                # Scene construction reads mutable model hand visibility too.
                with self.lock:
                    poses = {n: dict(p) for n, p in self.poses.items()}
                for name, pose in poses.items():
                    if name == 'wrist':
                        camera.lookat[:] = data.site_xpos[self.sim.tcp]
                        camera.lookat[2] += 0.02
                        camera.azimuth = math.degrees(math.atan2(data.site_xpos[self.sim.tcp, 1], data.site_xpos[self.sim.tcp, 0])) + 180
                        camera.elevation, camera.distance = -30, 0.22
                    else:
                        camera.lookat[:] = pose['lookat']
                        camera.azimuth, camera.elevation, camera.distance = pose['azimuth'], pose['elevation'], pose['distance']
                    with self.sim.lock:
                        renderer.update_scene(data, camera=camera)
                    rgb = renderer.render().copy()
                    renderer.enable_depth_rendering()
                    depth = renderer.render().copy()
                    renderer.disable_depth_rendering()
                    renderer.enable_segmentation_rendering()
                    segmentation = renderer.render().copy()
                    renderer.disable_segmentation_rendering()
                    # Stereo eye centres average to the monocular camera centre.
                    eyes = renderer.scene.camera
                    position = (np.array(eyes[0].pos) + eyes[1].pos) / 2
                    forward, up = np.array(eyes[0].forward), np.array(eyes[0].up)
                    right = np.cross(forward, up)
                    world_from_camera = np.eye(4)
                    world_from_camera[:3, :3] = np.column_stack((right, -up, forward))
                    world_from_camera[:3, 3] = position
                    f = self.height / (2 * math.tan(math.radians(self.sim.model.vis.global_.fovy) / 2))
                    metadata = {'name': name, 'time_s': float(data.time), 'timestamp': time.monotonic(),
                                'width':self.width, 'height':self.height, 'K': [[f,0,self.width/2],[0,f,self.height/2],[0,0,1]],
                                'dist': [0.,0.,0.,0.,0.],
                                'world_from_camera': world_from_camera.tolist(),
                                'camera_frame': 'OpenCV: x right, y down, z forward',
                                'depth': 'float32 metres along optical axis',
                                'segmentation': 'int32 [object_id, object_type]; background [-1,-1]',
                                'wrist_mode': 'TCP-following inspection view; not a calibrated wrist mount' if name == 'wrist' else None}
                    if name in self.intrinsics:
                        intrinsic = self.intrinsics[name]
                        if name not in self.maps:
                            yy, xx = np.mgrid[:self.height, :self.width].astype(np.float32)
                            pixels = np.stack((xx, yy), axis=-1).reshape(-1, 1, 2)
                            undistorted = cv2.undistortPoints(pixels, intrinsic.camera_matrix, intrinsic.dist,
                                                            P=np.array(metadata['K'])).reshape(self.height, self.width, 2)
                            self.maps[name] = (undistorted[:,:,0].copy(), undistorted[:,:,1].copy())
                        mx, my = self.maps[name]
                        rgb = cv2.remap(rgb, mx, my, cv2.INTER_LINEAR)
                        depth = cv2.remap(depth, mx, my, cv2.INTER_NEAREST)
                        segmentation = cv2.remap(segmentation.astype(np.float32), mx, my, cv2.INTER_NEAREST,
                                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(-1,-1)).astype(np.int32)
                        metadata['K'] = intrinsic.camera_matrix.tolist()
                        metadata['dist'] = intrinsic.dist.tolist()
                    image = io.BytesIO()
                    Image.fromarray(rgb).save(image, format='JPEG', quality=85)
                    with self.lock:
                        self.frames[name] = {'jpeg': image.getvalue(), 'rgb': rgb, 'depth': depth,
                                             'segmentation': segmentation, 'metadata': metadata}
                self.ready.set()
                self.stop.wait(max(0, self.period - (time.monotonic()-start)))
        except Exception as exc:
            with self.lock:
                self.error = f'{type(exc).__name__}: {exc}'
            self.ready.set()
        finally:
            if renderer:
                renderer.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
