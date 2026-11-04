"""Fixed, local service definitions. Request bodies never supply paths or commands."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PORTS = {'simulator': 8766, 'deck': 8765, 'sam': 8767, 'relay': 8772}
CAMERAS = {'ace': ('Insta360 Ace Pro', (640, 360), 'calibration/ace-pro.json'),
           'ugreen': ('UGREEN Camera', (1280, 720), 'calibration/ugreen.json')}
# Marker-frame metres. Refuse the preset if the active manual policy overlaps it.
PRESET = {'obj1': [.24, -.12], 'obj2': [.16, -.20]}
DEMO_DEFAULTS = {'red cube': [.23,.10,.018], 'blue cube': [.22,-.13,.018]}

@dataclass(frozen=True)
class Service:
    argv: tuple[str, ...]
    port: int | None = None
    health: str | None = None
    timeout: float = 40

class Config:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.logs = self.root/'recordings/launcher'
        self.workspace = self.root/'recordings/workspaces'
        self.gpu_lock = self.root/'recordings/.gpu.lock'
        self.workspace_python = self.root/'workspace_splat/.venv/bin/python'
        self.sim_python = self.root/'simulation/.venv/bin/python'
        self.hands_python = Path.home()/'src/hands-venv/bin/python'
        self.sam_python = Path.home()/'src/sam3-venv/bin/python'
        self.ports = dict(PORTS)
        self.preset = {k:list(v) for k,v in PRESET.items()}
        self.poll_s = .15
        self.gpu_timeout = 120
        self.gate_timeout = 55
        self.request_timeout = 2
        self.shutdown_timeout = 3

    def env(self):
        return {**os.environ, 'PYTHONPATH': str(self.root), 'PYTHONUNBUFFERED': '1',
                'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True'}

    def model_choices(self):
        try:
            result = subprocess.run([str(self.workspace_python), '-m', 'lookandpick.mission_control', '--help'],
                                    cwd=self.root, capture_output=True, text=True, timeout=8, env=self.env())
            match = re.search(r'--model\s+\{([\w,-]+)\}', result.stdout)
            if match:
                return [v for v in match[1].split(',') if re.fullmatch(r'[a-z][a-z0-9-]{0,40}', v)]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ['fake', 'spark']

    def service(self, name, *, live=False, options=None, cameras=None, ip=None):
        options = options or {'model':'fake', 'second_camera':True, 'sam_people':True}
        if name == 'simulator':
            return Service((str(self.sim_python), '-u', 'simulation/run.py'), self.ports[name], '/api/state')
        if name == 'sam':
            return Service((str(self.sam_python), 'scripts/sam_worker.py'), self.ports[name], '/health', 60)
        if name == 'deck':
            command = [str(self.hands_python if live else self.workspace_python), '-u', '-m',
                       'lookandpick.mission_control', '--backend', 'sim', '--model', options['model'],
                       '--port', str(self.ports[name])]
            if live:
                command += ['--hand-gate', str(cameras['ace']['index']), '--intrinsics', CAMERAS['ace'][2]]
                if options['second_camera']:
                    command += ['--second-camera', str(cameras['ugreen']['index']), '--second-intrinsics', CAMERAS['ugreen'][2]]
                if options['sam_people']:
                    command += ['--sam-people']
            return Service(tuple(command), self.ports[name], '/api/state', 70 if live else 30)
        if name == 'relay':
            if not ip:
                raise ValueError('No Wi-Fi IPv4 address; connect the phone and laptop to the same network')
            return Service((str(self.workspace_python), '-m', 'lookandpick.glasses_gateway', '--bind', ip,
                            '--public-host', ip, '--port', str(self.ports[name]), '--deck-url', self.url('deck'),
                            '--cert', 'recordings/glasses-gateway/tls/server.crt', '--key',
                            'recordings/glasses-gateway/tls/server.key'), self.ports[name], None, 10)
        if name == 'logs':
            return Service(('xdg-open',str(self.logs)),timeout=10)
        if name == 'selfcheck':
            return Service((str(self.workspace_python), '-u', 'scripts/sim_motion_check.py'), timeout=300)
        if name == 'tls':
            if not ip:
                raise ValueError('Connect to Wi-Fi before creating the certificate')
            return Service((str(self.workspace_python), 'scripts/create_glasses_tls.py', ip), timeout=25)
        raise ValueError('Unknown service')

    def url(self, name):
        return f'http://127.0.0.1:{self.ports[name]}'
