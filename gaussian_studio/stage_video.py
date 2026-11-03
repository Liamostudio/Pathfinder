"""Render labelled reconstruction stages on a shared path between input cameras."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image,ImageDraw,ImageFont


def video_writer(path,width,height,fps):
    import imageio_ffmpeg
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    return subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-n',
        '-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0',
        '-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)


def frame_with_label(rgb,title,note):
    height,width=rgb.shape[:2]
    frame=Image.new('RGB',(width,height+80),'#101618');frame.paste(Image.fromarray(rgb),(0,48))
    draw=ImageDraw.Draw(frame)
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',22)
        small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
    except OSError:font=small=ImageFont.load_default()
    draw.text((14,12),title,fill='#e8eee1',font=font)
    draw.text((14,height+56),note,fill='#bbc7b2',font=small)
    return frame


def camera_path(predictions,frames,width,height,order=None):
    from scipy.spatial.transform import Rotation,Slerp
    poses=predictions['extrinsics'];K=predictions['intrinsics']
    order=order if order is not None else [2,3,0,1,2] if len(poses)==4 else list(range(len(poses)))+[0]
    c2w=np.array([np.linalg.inv(p if p.shape==(4,4) else np.vstack([p,[0,0,0,1]])) for p in poses])
    rotations=Slerp(np.arange(len(order)),Rotation.from_matrix(c2w[order,:3,:3]))
    times=np.arange(frames)/frames*(len(order)-1)
    result=[]
    for time,rotation in zip(times,rotations(times).as_matrix()):
        i=min(int(time),len(order)-2);fraction=time-i
        centre=c2w[order[i],:3,3]*(1-fraction)+c2w[order[i+1],:3,3]*fraction
        pose=np.eye(4,dtype=np.float32);pose[:3,:3]=rotation.T;pose[:3,3]=-rotation.T@centre
        intrinsics=K[order[i]]*(1-fraction)+K[order[i+1]]*fraction
        intrinsics=intrinsics.copy();intrinsics[0]*=width/predictions['depth'].shape[2];intrinsics[1]*=height/predictions['depth'].shape[1]
        result.append((pose,intrinsics))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry',type=Path,required=True,help='MapAnything stage directory')
    parser.add_argument('--cloud',type=Path,help='Optional world-space points/colours npz')
    parser.add_argument('--trained',type=Path,help='Optional trained capture')
    parser.add_argument('--objects',type=Path,help='Optional separate observed object cloud')
    parser.add_argument('--object-note',help='Override the object-layer description, for example for known CAD geometry')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--title',required=True)
    parser.add_argument('--seconds',type=int,default=8);parser.add_argument('--fps',type=int,default=24)
    parser.add_argument('--camera-order',help='Comma-separated camera indices shared by comparison videos')
    parser.add_argument('--orbit',action='store_true',help='Full 360 diagnostic instead of the input-camera sweep')
    args=parser.parse_args()
    if args.output.exists():parser.error('Output already exists')
    if args.trained and args.cloud:parser.error('Choose one geometry source')
    with np.load(args.geometry/'predictions.npz',allow_pickle=False) as data:prediction={k:data[k] for k in data.files}
    from .depth_estimate import unproject
    if args.trained:
        scene=json.loads((args.trained/'output/scene.json').read_text())
        normalization=scene['normalization']
        with np.load(args.trained/'output/gaussians.npz',allow_pickle=False) as data:values={k:data[k] for k in data.files}
        note=f'Trained Gaussians | same input-camera sweep | {len(prediction["extrinsics"])} selected views; scale unverified'
        if args.objects:
            from workspace_splat.formats import SH_C0
            with np.load(args.objects,allow_pickle=False) as data:object_points,object_colours=data['points'],data['colors']
            count=len(object_points)
            object_values={'means':(object_points-np.asarray(normalization['centre']))/normalization['radius'],
                'colors':object_colours,'scales':np.full((count,3),.0025),'quats':np.tile([1,0,0,0],(count,1)),
                'opacities':np.full(count,.9)}
            if 'sh_coefficients' in values:
                coefficients=np.zeros((count,values['sh_coefficients'].shape[1],3));coefficients[:,0]=(object_colours-.5)/SH_C0
                object_values['sh_coefficients']=coefficients
            values={key:np.concatenate([value,object_values[key]]) for key,value in values.items()}
            note=args.object_note or 'Trained surface appearance + unchanged observed object points | relative scale'
    else:
        if args.cloud:
            with np.load(args.cloud,allow_pickle=False) as data:points,colors=data['points'],data['colors']
        else:
            with np.load(args.geometry/'consistency.npz',allow_pickle=False) as data:keep=data['keep']
            points=np.concatenate([unproject(d,k,e)[m] for d,k,e,m in zip(prediction['depth'],prediction['intrinsics'],prediction['extrinsics'],keep)])
            colors=np.concatenate([rgb[m] for rgb,m in zip(prediction['rgb'],keep)])/255
        finite=np.isfinite(points).all(1);points,colors=points[finite],colors[finite]
        if len(points)>300000:
            indices=np.random.default_rng(0).choice(len(points),300000,replace=False);points,colors=points[indices],colors[indices]
        centre=np.median(points,axis=0);radius=float(np.quantile(np.linalg.norm(points-centre,axis=1),.9))
        normalization={'centre':centre.tolist(),'radius':radius}
        # Fixed small isotropic kernels display estimated points; these are NOT a trained splat.
        values={'means':(points-centre)/radius,'colors':colors,'scales':np.full((len(points),3),.0025),
                'quats':np.tile([1,0,0,0],(len(points),1)),'opacities':np.full(len(points),.9)}
        note='Geometry preview with fixed point kernels | same input-camera sweep | unverified scale'
    from workspace_splat.backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    tensors={k:torch.tensor(v,device='cuda',dtype=torch.float32).contiguous() for k,v in values.items()}
    tensors['quats']=F.normalize(tensors['quats'],dim=-1)
    width,height=960,720;frames=args.seconds*args.fps
    centre=np.asarray(normalization['centre']);radius=normalization['radius']
    path=camera_path(prediction,frames,width,height,order=[int(i) for i in args.camera_order.split(',')] if args.camera_order else None)
    if args.orbit:
        from .turntable import camera_matrix
        with np.load(args.geometry/'consistency.npz',allow_pickle=False) as saved:keep=saved['keep']
        world=np.concatenate([unproject(d,k,e)[m] for d,k,e,m in zip(prediction['depth'],prediction['intrinsics'],prediction['extrinsics'],keep)])
        reference={'bounds':np.quantile(world,[.02,.98],axis=0).tolist(),
            'cameras':[{'world_to_camera':pose.tolist()} for pose in prediction['extrinsics']]}
        focal=height/(2*math.tan(math.radians(55)/2));K=np.array([[focal,0,width/2],[0,focal,height/2],[0,0,1]],dtype=np.float32)
        path=[(camera_matrix(reference,i*2*math.pi/frames),K) for i in range(frames)]
        note='Full 360 diagnostic | unseen room areas remain absent | relative scale'
    encoder=video_writer(args.output,width,height+80,args.fps)
    try:
        for i,(pose,K) in enumerate(path):
            pose[:3,3]=(pose[:3,:3]@centre+pose[:3,3])/radius
            with torch.inference_mode():
                colour=tensors.get('sh_coefficients',tensors['colors'])
                degree=int(math.sqrt(colour.shape[1])-1) if colour.ndim==3 else None
                rgb,_,_=rasterization(tensors['means'],tensors['quats'],tensors['scales'],tensors['opacities'],colour,
                    torch.tensor(pose,device='cuda',dtype=torch.float32)[None],torch.tensor(K,device='cuda',dtype=torch.float32)[None],width,height,
                    packed=False,sh_degree=degree,near_plane=.01,far_plane=100,backgrounds=torch.tensor([[.04,.05,.06]],device='cuda'))
                image=np.uint8(rgb[0].clamp(0,1).cpu().numpy()*255)
            frame=frame_with_label(image,args.title,note);encoder.stdin.write(frame.tobytes())
            if i==0:frame.save(args.output.with_suffix('.jpg'),quality=94)
        encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('Encoder failed')
    except BaseException:
        encoder.kill();encoder.wait();args.output.unlink(missing_ok=True);raise
    print(json.dumps({'video':str(args.output),'frames':frames,'seconds':args.seconds}),flush=True)


if __name__=='__main__':main()
