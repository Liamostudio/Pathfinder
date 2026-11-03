"""Render a deterministic 360-degree MP4 directly from trained Gaussian arrays.

Example: python -m gaussian_studio.turntable <capture> --output <video.mp4>
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import time
import numpy as np
from PIL import Image,ImageDraw


def orbit_setup(scene):
    bounds=np.asarray(scene['bounds'],dtype=np.float64)
    target=bounds.mean(axis=0)
    poses=np.asarray([c['world_to_camera'] for c in scene['cameras']],dtype=np.float64)
    up=-poses[:,1,:3].mean(axis=0);up/=np.linalg.norm(up)
    pose=poses[-1];centre=-pose[:3,:3].T@pose[:3,3]
    offset=centre-target;horizontal=offset-up*np.dot(offset,up)
    radius=max(float(np.linalg.norm(horizontal))*1.28,float(np.linalg.norm(bounds[1]-bounds[0]))*.6)
    horizontal/=np.linalg.norm(horizontal)
    tangent=np.cross(up,horizontal);tangent/=np.linalg.norm(tangent)
    return target,up,horizontal,tangent,radius


def camera_matrix(scene,angle):
    target,up,base,tangent,radius=orbit_setup(scene)
    centre=target+radius*(math.cos(angle)*base+math.sin(angle)*tangent)+up*radius*.28
    forward=target-centre;forward/=np.linalg.norm(forward)
    right=np.cross(forward,up);right/=np.linalg.norm(right)
    down=np.cross(forward,right)
    rotation=np.stack([right,down,forward]);pose=np.eye(4,dtype=np.float32)
    pose[:3,:3]=rotation;pose[:3,3]=-rotation@centre
    return pose


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=12);parser.add_argument('--fps',type=int,default=30)
    parser.add_argument('--width',type=int,default=1920);parser.add_argument('--height',type=int,default=1080)
    parser.add_argument('--preview',action='store_true',help='Render an eight-angle contact sheet instead of video')
    args=parser.parse_args()
    if not 1<=args.seconds<=120 or not 1<=args.fps<=60 or args.width%2 or args.height%2 or not 128<=args.width<=3840 or not 128<=args.height<=2160:
        parser.error('Use 1–120 seconds, 1–60 fps and even dimensions within 128–3840 by 128–2160')
    if args.output.exists():parser.error('Output already exists; choose a new filename')
    from workspace_splat.backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    if not torch.cuda.is_available():raise SystemExit('CUDA is required to render the Gaussian turntable')
    scene=json.loads((args.capture/'output/scene.json').read_text())
    with np.load(args.capture/'output/gaussians.npz',allow_pickle=False) as arrays:
        tensors={name:torch.as_tensor(arrays[name],device='cuda',dtype=torch.float32).contiguous() for name in arrays.files}
    tensors['quats']=F.normalize(tensors['quats'],dim=-1)
    width,height=args.width,args.height
    focal=height/(2*math.tan(math.radians(55)/2))
    K=torch.tensor([[[focal,0,width/2],[0,focal,height/2],[0,0,1]]],device='cuda',dtype=torch.float32)
    background=torch.tensor([[.05,.06,.07]],device='cuda')
    def render(angle):
        pose=torch.as_tensor(camera_matrix(scene,angle),device='cuda')[None]
        with torch.inference_mode():
            rgb,_,_=rasterization(tensors['means'],tensors['quats'],tensors['scales'],tensors['opacities'],tensors['colors'],
                                 pose,K,width,height,packed=False,near_plane=.01,far_plane=100,backgrounds=background)
            return (rgb[0].clamp(0,1)*255).to(torch.uint8).cpu().numpy()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.preview:
        sheet=Image.new('RGB',(width*4,height*2+44),'#0d1012')
        draw=ImageDraw.Draw(sheet)
        for i in range(8):
            image=Image.fromarray(render(i*2*math.pi/8))
            x=(i%4)*width;y=(i//4)*(height+22)
            sheet.paste(image,(x,y));draw.text((x+10,y+height+4),f'{i*45} degrees',fill='#c6cfbf')
        sheet.save(args.output);print('Preview: '+str(args.output),flush=True);return
    import imageio_ffmpeg
    frames=round(args.seconds*args.fps);started=time.monotonic()
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-f','rawvideo','-vcodec','rawvideo',
             '-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(args.fps),'-i','pipe:0','-an',
             '-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',str(args.output)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE)
    try:
        for i in range(frames):
            process.stdin.write(render(i*2*math.pi/frames).tobytes())
            if i%args.fps==0:print(json.dumps({'frame':i,'frames':frames,'elapsed_s':round(time.monotonic()-started,1)}),flush=True)
        process.stdin.close()
        if process.wait()!=0:raise RuntimeError('Video encoder failed')
    except BaseException:
        process.kill();process.wait();args.output.unlink(missing_ok=True);raise
    print(json.dumps({'video':str(args.output),'frames':frames,'seconds':frames/args.fps,'width':width,'height':height,'bytes':args.output.stat().st_size}),flush=True)


if __name__=='__main__':main()
