"""Compare two co-registered splats using identical cameras and export a video."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from .turntable import camera_matrix


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path);parser.add_argument('candidate',type=Path)
    parser.add_argument('--depth',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=12);parser.add_argument('--fps',type=int,default=24)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    if not 1<=args.seconds<=60 or not 1<=args.fps<=30:parser.error('Invalid video duration or frame rate')
    scenes=[json.loads((p/'output/scene.json').read_text()) for p in (args.baseline,args.candidate)]
    if not np.allclose(scenes[0]['normalization']['centre'],scenes[1]['normalization']['centre']) or not np.isclose(scenes[0]['normalization']['radius'],scenes[1]['normalization']['radius']):
        parser.error('Scenes must use the same normalization')
    args.output.mkdir(parents=True)
    from workspace_splat.backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    tensors=[]
    for path in (args.baseline,args.candidate):
        with np.load(path/'output/gaussians.npz',allow_pickle=False) as saved:
            values={k:torch.tensor(saved[k],device='cuda',dtype=torch.float32).contiguous() for k in saved.files}
        values['quats']=F.normalize(values['quats'],dim=-1);tensors.append(values)
    background=torch.tensor([[.05,.06,.07]],device='cuda')
    def render(which,pose,K,width,height,depth=False):
        t=tensors[which]
        with torch.inference_mode():
            rgb,alpha,_=rasterization(t['means'],t['quats'],t['scales'],t['opacities'],t['colors'],
                torch.tensor(pose,device='cuda',dtype=torch.float32)[None],
                torch.tensor(K,device='cuda',dtype=torch.float32)[None],width,height,
                packed=False,near_plane=.01,far_plane=100,backgrounds=background,render_mode='RGB+ED' if depth else 'RGB')
            return rgb[0].cpu().numpy(),alpha[0,...,0].cpu().numpy()
    report={'baseline':args.baseline.name,'candidate':args.candidate.name,
            'comparison':'Identical orbit cameras, intrinsics, lighting and shared source normalization.',
            'evaluation':'Training-view appearance and agreement with the SAME guiding model depths; no held-out photos or measured depth.',
            'training':[]}
    for scene in scenes:
        report['training'].append({'steps':scene['steps'],'gaussians':scene['count'],
            'mean_training_psnr_db':float(np.mean([v['psnr_db'] for v in scene['training']['view_metrics']])),
            'elapsed_s':scene['training']['history'][-1]['elapsed_s'],
            'depth_guidance':scene['training'].get('depth_guidance')})
    with np.load(args.depth/'predictions.npz',allow_pickle=False) as saved:depth_data={k:saved[k] for k in saved.files}
    with np.load(args.depth/'consistency.npz',allow_pickle=False) as saved:keep=saved['keep']
    depth_report=json.loads((args.depth/'report.json').read_text())
    centre=np.array(scenes[0]['normalization']['centre']);radius=scenes[0]['normalization']['radius']
    per_view=[];all_errors=[[],[]]
    for i,name in enumerate(depth_report['image_names']):
        pose=np.eye(4,dtype=np.float32);pose[:3]=depth_data['extrinsics'][i,:3]
        pose[:3,3]=(pose[:3,:3]@centre+pose[:3,3])/radius
        target=depth_data['depth'][i]/radius;height,width=target.shape
        predictions=[render(j,pose,depth_data['intrinsics'][i],width,height,True) for j in range(2)]
        common=keep[i]&(predictions[0][1]>.5)&(predictions[1][1]>.5)
        view={'name':name,'common_supported_pixels':int(common.sum()),'models':[]}
        for j,(rgb,alpha) in enumerate(predictions):
            error=np.abs(rgb[...,3][common]-target[common])/target[common]
            all_errors[j].append(error)
            view['models'].append({'supported_pixel_coverage':float((alpha[keep[i]]>.5).mean()),
                                   'median_relative_depth_error':float(np.median(error)) if len(error) else None})
        per_view.append(view)
    report['depth_agreement']={'views':per_view,'common_mask_median_relative_error':[float(np.median(np.concatenate(errors))) for errors in all_errors],
                               'note':'Agreement with a model used in training is not evidence of independent geometric accuracy.'}
    (args.output/'comparison.json').write_text(json.dumps(report,indent=2))
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',23)
        small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
    except OSError:font=small=ImageFont.load_default()
    width,height=960,720;top,bottom=44,28
    focal=height/(2*math.tan(math.radians(55)/2))
    K=np.array([[focal,0,width/2],[0,focal,height/2],[0,0,1]],dtype=np.float32)
    def pair(angle):
        canvas=Image.new('RGB',(width*2,height+top+bottom),'#0d1012');draw=ImageDraw.Draw(canvas)
        pose=camera_matrix(scenes[0],angle)
        labels=(f"Original | RGB only | {scenes[0]['steps']:,} steps",f"Depth-guided | DA3 | {scenes[1]['steps']:,} steps")
        for j,label in enumerate(labels):
            rgb,_=render(j,pose,K,width,height)
            canvas.paste(Image.fromarray(np.uint8(np.clip(rgb,0,1)*255)),(j*width,top))
            draw.text((j*width+18,9),label,fill='#ecf0e7',font=font)
        draw.text((18,top+height+4),'Identical orbit cameras. Four source photos; unseen surfaces remain incomplete.',fill='#b5beb1',font=small)
        return canvas
    sheet=Image.new('RGB',(1280,528*3),'#0d1012')
    for row,degrees in enumerate((0,90,180)):
        sheet.paste(pair(math.radians(degrees)).resize((1280,528),Image.Resampling.LANCZOS),(0,row*528))
    sheet.save(args.output/'comparison.jpg',quality=94)
    # Also compare known views against their actual source photograph.
    sw,sh=560,420;head=40
    sheet=Image.new('RGB',(sw*3,head+sh*2),'#0d1012');draw=ImageDraw.Draw(sheet)
    for j,label in enumerate(('Source photograph','Original RGB-only splat','Depth-guided splat')):draw.text((j*sw+10,10),label,fill='#ecf0e7',font=small)
    for row,index in enumerate((2,3)):
        camera=scenes[0]['cameras'][index];w,h=camera['width'],camera['height']
        with Image.open(args.baseline/'undistorted/images'/camera['name']) as source:
            source=source.convert('RGB').resize((w,h),Image.Resampling.LANCZOS)
        images=[source]+[Image.fromarray(np.uint8(np.clip(render(j,camera['world_to_camera'],camera['K'],w,h)[0],0,1)*255)) for j in range(2)]
        for column,picture in enumerate(images):
            picture.thumbnail((sw,sh));sheet.paste(picture,(column*sw+(sw-picture.width)//2,head+row*sh+(sh-picture.height)//2))
    sheet.save(args.output/'training-views.jpg',quality=94)
    import imageio_ffmpeg
    video=args.output/'comparison.mp4';frames=args.seconds*args.fps
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-f','rawvideo','-vcodec','rawvideo',
             '-pix_fmt','rgb24','-s',f'{width*2}x{height+top+bottom}','-r',str(args.fps),'-i','pipe:0','-an',
             '-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',str(video)]
    encoder=subprocess.Popen(command,stdin=subprocess.PIPE)
    try:
        for i in range(frames):
            encoder.stdin.write(pair(i*2*math.pi/frames).tobytes())
            if i%args.fps==0:print(json.dumps({'stage':'video','frame':i,'frames':frames}),flush=True)
        encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('Video encoder failed')
    except BaseException:
        encoder.kill();encoder.wait();video.unlink(missing_ok=True);raise
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
