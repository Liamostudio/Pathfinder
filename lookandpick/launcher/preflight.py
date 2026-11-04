"""Small, replaceable read-only probes; importing this module opens no devices."""
import fcntl
import json
from pathlib import Path
import re
import socket
import subprocess
import urllib.error
import urllib.request

from .config import CAMERAS
from .supervisor import port_owner


def run(args, *, timeout=4, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)


def gpu_lease(path):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            stream.seek(0)
            try:owner=json.load(stream).get('owner', 'another GPU job')
            except (ValueError, TypeError):owner='another GPU job'
            return {'free':False, 'owner':str(owner)[:150]}
        fcntl.flock(stream, fcntl.LOCK_UN)
    return {'free':True, 'owner':None}  # Old metadata alone is not a held lock.


def camera_nodes(sysroot=Path('/sys/class/video4linux')):
    result = {}
    for name, (wanted, size, lens) in CAMERAS.items():
        candidates = []
        for path in Path(sysroot).glob('video*'):
            try:
                label=(path/'name').read_text().strip()
                # USB capture nodes expose index 0; ignore metadata/alternate nodes.
                index=(path/'index').read_text().strip()
                if wanted.casefold() in label.casefold() and index=='0':
                    candidates.append(int(path.name.removeprefix('video')))
            except (OSError, ValueError):
                continue
        if candidates:
            result[name]={'index':min(candidates), 'name':wanted, 'size':list(size), 'lens':lens}
    return result


def camera_probe(index, width, height):
    # Executed ONLY in hands-venv in a bounded subprocess, never at import time.
    import cv2
    from lookandpick.calibration import detect_marker
    capture=cv2.VideoCapture(index, cv2.CAP_V4L2)
    try:
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width);capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok,frame=capture.read()
        if not ok or frame is None:
            return {'ok':False, 'message':'No frame; re-seat the cable and stop any other camera process'}
        actual=[frame.shape[1],frame.shape[0]]
        if actual!=[width,height]:
            return {'ok':False,'message':f'Camera delivered {actual[0]}×{actual[1]}, expected {width}×{height}; re-seat the cable'}
        import base64
        thumb=cv2.resize(frame,(240,round(240*height/width)))
        encoded,jpeg=cv2.imencode('.jpg',thumb,[cv2.IMWRITE_JPEG_QUALITY,65])
        return {'ok':True,'marker':detect_marker(frame) is not None,'size':actual,
                'thumbnail':'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode() if encoded else None}
    finally:
        capture.release()


class Probes:
    def __init__(self, config):self.config=config
    def cameras(self):return camera_nodes()
    def frame(self, camera):
        try:
            reply=run([str(self.config.hands_python),'-m','lookandpick.launcher.preflight','--camera',str(camera['index']),
                       *map(str,camera['size'])], timeout=7, cwd=self.config.root, env=self.config.env())
            return json.loads(reply.stdout.splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return {'ok':False,'message':'Camera probe failed or timed out; re-seat the cable, stop the old camera service and check hands-venv'}
    def lens(self, relative, size):
        try:
            from lookandpick.intrinsics import Intrinsics
            actual=Intrinsics.load(self.config.root/relative).size
            return tuple(actual)==tuple(size)
        except (OSError, ValueError, KeyError, TypeError, ImportError):return False
    def gpu(self):
        lease=gpu_lease(self.config.gpu_lock)
        try:
            result=run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],timeout=3)
            free=int(result.stdout.splitlines()[0])
            return {**lease,'available':True,'free_mb':free,'enough':free>=2800}
        except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
            return {**lease,'available':False,'free_mb':None,'enough':False}
    def network(self):
        addresses=[]
        try:
            rows=json.loads(run(['ip','-j','-4','address'],timeout=3).stdout)
            for row in rows:
                if (Path('/sys/class/net')/row['ifname']/'wireless').exists() or row['ifname'].startswith(('wl','wlan')):
                    addresses += [a['local'] for a in row.get('addr_info',[]) if a.get('family')=='inet']
        except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):pass
        reachable=False
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(urllib.request.Request('https://api.meta.ai',method='HEAD'),timeout=3) as response:
                reachable=response.status<500
        except urllib.error.HTTPError as exc:reachable=exc.code<500
        except (OSError,urllib.error.URLError):pass
        return {'ip':addresses[0] if addresses else None,'meta_reachable':reachable}
    def microphone(self):
        try:
            result=run(['arecord','-l'],timeout=3)
            return result.returncode==0 and 'card ' in result.stdout
        except (OSError,subprocess.TimeoutExpired):return False
    def weights(self):
        return {'hands':(self.config.root/'weights/hand_landmarker.task').is_file(),
                'sam':any((Path.home()/'.cache/huggingface/hub/models--facebook--sam3.1/snapshots').glob('*/sam3.1_multiplex.pt')),
                'people_optional':any((self.config.root/'weights').glob('*efficientdet*'))}
    def secrets(self):
        try:
            text=(self.config.root/'.env').read_text()
            return any(re.match(r'^\s*(?:export\s+)?MODEL_API_KEY\s*=\s*[^\s#\"\']',line) or
                       re.match(r'^\s*(?:export\s+)?MODEL_API_KEY\s*=\s*[\"\'][^\"\']+[\"\']',line) for line in text.splitlines())
        except OSError:return False
    def ports(self):
        ports={name:port_owner(port) for name,port in self.config.ports.items()}
        if not ports.get('deck'):
            for port in self.config.alternate_deck_ports:
                owner=port_owner(port)
                if owner:
                    ports['deck']={**owner,'port':port};break
        return ports


def workspace_status(config):
    from lookandpick.workspaces.service import WorkspaceService
    from lookandpick.zones import from_areas
    service=WorkspaceService(config.workspace)
    active=service.repo.active()
    result={'active':None,'manual_count':0,'warnings':[], 'jobs':[]}
    for value in service.repo.list():
        for job_id in value.get('jobs',[]):
            try:
                job=service.job(value['id'],job_id)
                if job['state'] in ('running','queued'):
                    result['jobs'].append({'workspace':value['id'],'id':job_id,'stage':job.get('stage'),'state':job['state']})
            except (OSError,ValueError,KeyError):pass
    if active:
        result['active']={'id':active['id'],'version':active['version_id'],'ready':not active.get('needs_rebuild',False)}
        if (active.get('working_area') or {}).get('enabled'):
            result['manual_count']=len(service.areas.snapshot(active['id'],active['version_id'])['manual_blocked'])
        guard=from_areas(service.areas)
        for name,xy in config.preset.items():
            reason=guard.violation((*xy,.018),(*xy,.018))
            if reason:result['warnings'].append(f'{name} preset: {reason}; clear testing cubes or redraw them before filming')
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--camera',nargs=3,type=int,required=True)
    args=parser.parse_args();print(json.dumps(camera_probe(*args.camera)))
