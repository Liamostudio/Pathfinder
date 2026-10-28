"""Standard-library HTTP Arm/Tracker bridge; importable without MuJoCo/numpy."""
import json
import math
import time
import urllib.request
from urllib.parse import urlsplit
from lookandpick.world import Hand, TrackedObject


class SimulationClient:
    def __init__(self, url='http://127.0.0.1:8766', *, command_timeout=30):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username or parsed.password or parsed.path not in ('', '/'):
            raise ValueError('Simulation bridge must use loopback HTTP')
        self.url = url.rstrip('/')
        self.command_timeout = command_timeout

    def _get(self, path):
        with urllib.request.urlopen(self.url+path,timeout=3) as response:
            return json.load(response)

    def state(self):
        return self._get('/api/state')

    @property
    def position(self):
        return tuple(self.state()['scene']['arm_position'])

    def objects(self):
        return [TrackedObject(**o) for o in self.state()['scene']['objects']]

    def hands(self):
        return [Hand(**h) for h in self.state()['scene']['hands']]

    def action(self, action, **values):
        request = urllib.request.Request(self.url+'/api/action', json.dumps({'action':action,**values},allow_nan=False).encode(),
                                         {'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:
            job = json.load(response)
        if 'job_id' not in job:
            return job
        deadline = time.monotonic() + self.command_timeout
        try:
            while time.monotonic() < deadline:
                status = self._get('/api/jobs/'+job['job_id'])
                if status['status'] != 'running':
                    return status
                time.sleep(.03)
        except Exception:
            self.halt()
            raise
        self.halt()
        raise TimeoutError('Simulation command timed out; stop requested')

    def move_to(self, target, speed_scale):
        result = self.action('move', position=list(target), speed_scale=speed_scale)
        return result.get('status') == 'completed' and result.get('result') is True

    def grip(self, closed):
        result = self.action('grip',closed=closed)
        return result.get('status') == 'completed' and result.get('result') is True

    def halt(self):
        self.action('stop')

    def set_speed_scale(self, scale):
        self.action('speed',scale=scale)

    def camera_frame(self, name='camera_a'):
        """Returns synchronised RGB, optical depth (m), segmentation, K and pose."""
        import io
        import numpy as np
        with urllib.request.urlopen(self.url+f'/api/cameras/{name}/frame.npz',timeout=5) as response:
            with np.load(io.BytesIO(response.read()),allow_pickle=False) as archive:
                return {key:archive[key] for key in archive.files}


class SimulationCamera:
    """CameraSource-compatible BGR frames for existing OpenCV/SAM pipelines."""
    def __init__(self, client=None, name='camera_a', fps=10):
        if not math.isfinite(fps) or not 0 < fps <= 30:
            raise ValueError('fps must be in (0,30]')
        self.client = client or SimulationClient()
        self.name, self.period = name, 1/fps
        self.closed = False

    def frames(self):
        previous = -1
        while not self.closed:
            started = time.monotonic()
            frame = self.client.camera_frame(self.name)
            stamp = float(frame['timestamp'])
            if stamp > previous:
                previous = stamp
                yield stamp, frame['rgb'][:,:,::-1].copy()
            time.sleep(max(0,self.period-(time.monotonic()-started)))

    def close(self):
        self.closed = True
