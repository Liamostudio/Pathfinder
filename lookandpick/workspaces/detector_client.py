"""One bounded owned CPU detector subprocess, separate from the HTTP runtime."""
import atexit
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time
from .runtimes import ROOT


class DetectorClient:
    def __init__(self):
        self.python=Path(os.environ.get('PATHFINDER_PEOPLE_PYTHON',str(Path.home()/'src/hands-venv/bin/python'))).expanduser()
        self.process=None;self.lock=threading.Lock();atexit.register(self.close)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process=None

    def detect(self,jpeg,source='default'):
        if not self.lock.acquire(blocking=False):raise ValueError('People/hand detector is busy; wait for the next frame')
        try:
            if not self.python.is_file():raise ValueError('People/hand CPU runtime missing. Configure PATHFINDER_PEOPLE_PYTHON with the existing MediaPipe environment')
            for name in ('hand_landmarker.task','efficientdet_lite0.tflite'):
                if not (ROOT/'weights'/name).is_file():raise ValueError('Cached person/hand weights are missing; restore the existing Pathfinder Vision models')
            if self.process is None or self.process.poll() is not None:
                folder=ROOT/'recordings';folder.mkdir(exist_ok=True)
                with (folder/'voxel-detector.log').open('ab') as log:
                    self.process=subprocess.Popen([str(self.python),'-u','-m','lookandpick.workspaces.people_hands'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
            self.process.stdin.write(json.dumps({'jpeg':jpeg,'source':source})+'\n');self.process.stdin.flush()
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                ready,_,_=select.select([self.process.stdout],[],[],max(0,deadline-time.monotonic()))
                if not ready:break
                line=self.process.stdout.readline()
                if not line:break
                try:result=json.loads(line)
                except ValueError:continue
                if not isinstance(result,dict):continue
                if result.get('error'):raise ValueError(result['error'])
                if 'detections' in result:return result
            self.close();raise ValueError('People/hand detection timed out or stopped; occupancy remains unknown')
        except (OSError,BrokenPipeError):self.close();raise ValueError('People/hand CPU worker failed; check the configured runtime') from None
        finally:self.lock.release()
