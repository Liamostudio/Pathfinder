"""Real CPU media adapter checks; no model downloads or CUDA calls."""
import json
import subprocess
import sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from lookandpick.workspaces.repository import atomic_json
from lookandpick.workspaces.schema import settings


def test_video_budget_is_explicit_and_does_not_truncate_photo_sources(tmp_path):
    sources=[]
    rng=np.random.default_rng(47)
    for i in range(6):
        image=tmp_path/f'photo-{i}.png';Image.fromarray(rng.integers(0,256,(256,320,3),dtype=np.uint8)).save(image);sources.append(str(image))
    video=tmp_path/'clip.avi';writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),4,(320,256))
    assert writer.isOpened()
    for i in range(12):writer.write(rng.integers(0,256,(256,320,3),dtype=np.uint8))
    writer.release();sources.append(str(video))
    request=tmp_path/'request.json';out=tmp_path/'capture'
    atomic_json(request,{'sources':sources,'output':str(out),'settings':settings({'video_fps':2.,'video_frames':3})})
    subprocess.run([sys.executable,'-m','lookandpick.workspaces.ingestion',str(request)],check=True,capture_output=True)
    manifest=json.loads((out/'capture.json').read_text())
    assert len(manifest['images'])==9
    frames=[x for x in manifest['images'] if x['timestamp_s'] is not None]
    assert len(frames)==3 and [x['timestamp_s'] for x in frames]==[0,.5,1.]
    assert all(Path(s).is_file() for s in sources)
    assert manifest['parameters']['max_frames']==3 and manifest['parameters']['all_images'] is True
