"""Normalise photographs or sampled video frames into a private capture dataset."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageOps

IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.webp','.tif','.tiff','.bmp'}
VIDEO_EXTENSIONS={'.mp4','.mov','.mkv','.avi','.webm','.m4v'}


def ingest(sources, output, *, max_edge=1280, fps=2., max_frames=160, min_images=2):
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Capture directory must be new or empty')
    if not 256 <= max_edge <= 4096 or not math.isfinite(fps) or not 0 < fps <= 30 or not 2 <= max_frames <= 1000:
        raise ValueError('Invalid capture resolution, sampling rate or frame limit')
    paths=[]
    for source in map(Path,sources):
        paths.extend(sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS|VIDEO_EXTENSIONS) if source.is_dir() else [source])
    if not paths:
        raise ValueError('No images or videos found')
    images=output/'images';images.mkdir(parents=True)
    seen=set();records=[];skipped=[]
    def store(image, source, stamp=None):
        image.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        rgb=np.array(image.convert('RGB'))
        digest=hashlib.sha256(rgb.tobytes()).hexdigest()
        if digest in seen:
            skipped.append({'source':source.name,'reason':'exact duplicate'});return
        seen.add(digest)
        name=f'{len(records):05d}.jpg'
        Image.fromarray(rgb).save(images/name,quality=95)
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
        sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
        records.append({'name':name,'source':source.name,'timestamp_s':stamp,'width':rgb.shape[1],
                        'height':rgb.shape[0],'sha256':digest,'sharpness':sharpness})
    for path in paths:
        if len(records)>=max_frames:break
        if not path.is_file():raise ValueError(f'Input does not exist: {path}')
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            with Image.open(path) as image:
                store(ImageOps.exif_transpose(image).convert('RGB'),path)
        elif path.suffix.lower() in VIDEO_EXTENSIONS:
            cap=cv2.VideoCapture(str(path))
            try:
                if not cap.isOpened():raise ValueError(f'Cannot decode video: {path.name}')
                rate=cap.get(cv2.CAP_PROP_FPS)
                if not math.isfinite(rate) or rate<=0:raise ValueError('Video has invalid frame rate')
                sidecar=path.with_suffix('.json')
                stamps=json.loads(sidecar.read_text()) if sidecar.exists() else None
                if stamps is not None and (not isinstance(stamps,list) or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in stamps)):
                    raise ValueError('Invalid video timestamp sidecar')
                i=0;next_time=0.
                while len(records)<max_frames:
                    ok,frame=cap.read()
                    if not ok:break
                    if stamps is not None and i>=len(stamps):raise ValueError('Video timestamp sidecar too short')
                    if i/rate+1e-9>=next_time:
                        store(Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)),path,stamps[i] if stamps else i/rate)
                        next_time+=1/fps
                    i+=1
            finally:cap.release()
        else:raise ValueError(f'Unsupported input: {path.name}')
    if len(records)<min_images:raise ValueError(f'At least {min_images} distinct image frames are required')
    manifest={'version':1,'images':records,'skipped':skipped,'warnings':[],'parameters':{'max_edge':max_edge,'fps':fps,'max_frames':max_frames}}
    if len(records)<20 and min_images>1:manifest['warnings'].append('Sparse capture: use 40–120 overlapping views or a slow walk-around video for better coverage.')
    if any(r['sharpness']<30 for r in records):manifest['warnings'].append('Some frames have weak texture or blur; inspect capture sharpness.')
    (output/'capture.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def ingest_frames(source, output, *, max_frames=120, max_edge=1280):
    """Capture an existing FrameSource's (timestamp_s, BGR) frames for a scan.

    The source must move through viewpoints; a fixed camera cannot reveal unseen
    geometry. Closes the frame iterator but does not take ownership of the source.
    """
    import tempfile
    if not isinstance(max_frames,int) or not 2<=max_frames<=1000:
        raise ValueError('Frame limit must be an integer in [2,1000]')
    output=Path(output)
    iterator=source.frames() if hasattr(source,'frames') else iter(source)
    stamps=[]
    try:
        with tempfile.TemporaryDirectory() as directory:
            for stamp,frame in iterator:
                if not math.isfinite(stamp) or (stamps and stamp<=stamps[-1]):
                    raise ValueError('Frame timestamps must be finite and strictly increasing')
                image=np.asarray(frame)
                if image.ndim!=3 or image.shape[2]!=3 or image.dtype!=np.uint8:
                    raise ValueError('FrameSource must yield uint8 BGR frames')
                name=f'{len(stamps):05d}.png'
                if not cv2.imwrite(str(Path(directory)/name),image):raise RuntimeError('Could not stage captured frame')
                stamps.append(float(stamp))
                if len(stamps)>=max_frames:break
            manifest=ingest([Path(directory)],output,max_frames=max_frames,max_edge=max_edge)
            for record in manifest['images']:
                record['timestamp_s']=stamps[int(Path(record['source']).stem)]
                record['source']=getattr(source,'name','FrameSource')
            (output/'capture.json').write_text(json.dumps(manifest,indent=2)+'\n')
            return manifest
    finally:
        if hasattr(iterator,'close'):iterator.close()
