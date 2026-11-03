"""Persistent CPU detector adapter using the existing Pathfinder Vision models.

JSON lines over owned pipes; no camera, GPU, network or motor access.
"""
import base64
import json
import os
from pathlib import Path
import sys
import threading
import time


def start_parent_watchdog(interval=.25):
    """Track process lifetime, independent of a short-lived spawning thread."""
    parent=os.getppid()
    def watch_parent():
        while os.getppid()==parent:time.sleep(interval)
        os._exit(0)
    thread=threading.Thread(target=watch_parent,daemon=True);thread.start();return thread


def main():
    # Linux PDEATHSIG follows the spawning thread. HTTP request threads end
    # after a response; the detector belongs to their process instead.
    start_parent_watchdog()
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'tools/pathfinder-vision'))
    import cv2
    import numpy as np
    from pathfinder_vision.detectors import HandDetector,PersonBoxDetector
    detectors={}
    try:
        for line in sys.stdin:
            try:
                request=json.loads(line);source=request.get('source','default')
                if source not in detectors:
                    if len(detectors)>=4:
                        old=detectors.pop(next(iter(detectors)))
                        for detector in old:detector.close()
                    detectors[source]=(HandDetector(root/'weights/hand_landmarker.task',max_hands=4,confidence=.5),PersonBoxDetector(root/'weights/efficientdet_lite0.tflite',confidence=.35,max_people=8))
                hands,people=detectors[source];raw=base64.b64decode(request['jpeg'],validate=True)
                image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
                if image is None or max(image.shape[:2])>1280:raise ValueError('Use a decoded camera frame no larger than 1280 pixels')
                started=time.monotonic();stamp=int(started*1000);detections=hands.detect(image,stamp)+people.detect(image,stamp)
                height,width=image.shape[:2];result=[]
                for d in detections:
                    if d.kind not in ('hand','person'):continue
                    x0,y0,x1,y1=d.box
                    result.append({'kind':d.kind,'confidence':d.score,'box':[max(0,x0/width),max(0,y0/height),min(1,x1/width),min(1,y1/height)]})
                print(json.dumps({'detections':result,'width':width,'height':height,'inference_ms':round((time.monotonic()-started)*1000,1),'models':['MediaPipe Hand Landmarker','EfficientDet-Lite0 / person only']}),flush=True)
            except (ValueError,KeyError,TypeError) as e:print(json.dumps({'error':str(e)[:200]}),flush=True)
    finally:
        for pair in detectors.values():
            for detector in pair:detector.close()

if __name__=='__main__':main()
