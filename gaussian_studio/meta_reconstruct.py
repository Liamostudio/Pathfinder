"""Local MapAnything joint reconstruction, with reproducible camera metadata."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image
from .depth_estimate import unproject,view_consistency,export_cloud,export_surfaces,previews,ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',default='facebook/map-anything-apache')
    parser.add_argument('--offline',action='store_true');parser.add_argument('--source-revision');parser.add_argument('--revision');parser.add_argument('--resolution',type=int,default=518)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    import os,sys
    if args.offline:
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    if args.source_revision:
        source=ROOT/'.cache'/('map-anything-'+args.source_revision)
        if not source.is_dir():raise RuntimeError('Pinned MapAnything source cache missing')
        sys.path.insert(0,str(source))
    import torch
    from huggingface_hub import HfApi
    from mapanything.models import MapAnything
    from mapanything.utils.image import load_images
    from mapanything.utils.cropping import crop_resize_if_necessary
    torch.set_num_threads(8)
    if not torch.cuda.is_available():raise RuntimeError('CUDA required for this reconstruction')
    manifest=json.loads((args.capture/'capture.json').read_text());names=[i['name'] for i in manifest['images']]
    revision=args.revision or (None if args.offline else HfApi().model_info(args.model).sha)
    if not revision:raise ValueError('Offline reconstruction requires a pinned revision')
    print(json.dumps({'stage':'loading','model':args.model,'revision':revision,'views':len(names)}),flush=True)
    model=MapAnything.from_pretrained(args.model,revision=revision,cache_dir=str(ROOT/'.cache/huggingface')).to('cuda').eval()
    images=[str(args.capture/'images'/n) for n in names]
    views=load_images(images,resize_mode='longest_side',size=args.resolution,verbose=True)
    torch.cuda.reset_peak_memory_stats();started=time.monotonic()
    with torch.inference_mode():
        result=model.infer(views,memory_efficient_inference=True,minibatch_size=1,use_amp=True,amp_dtype='bf16',
                           apply_mask=True,mask_edges=True,apply_confidence_mask=False)
    torch.cuda.synchronize();elapsed=time.monotonic()-started
    def array(key):return np.stack([v[key][0].float().cpu().numpy() for v in result])
    depth=array('depth_z')[...,0];confidence=array('conf');K=array('intrinsics');cam_to_world=array('camera_poses')
    poses=np.linalg.inv(cam_to_world).astype(np.float32)
    rgb=np.uint8(np.clip(array('img_no_norm'),0,1)*255)
    if rgb.shape[1]==3:rgb=rgb.transpose(0,2,3,1)
    model_mask=array('mask')[...,0]>.5
    full_K=[];image_transforms=[]
    for i,path in enumerate(images):
        with Image.open(path) as image:
            w,h=image.size
            dummy=np.array([[1000.,0,w/2],[0,1000.,h/2],[0,0,1]],dtype=np.float32)
            processed=crop_resize_if_necessary(image,resolution=(depth.shape[2],depth.shape[1]),intrinsics=dummy)
        transform=processed[1]@np.linalg.inv(dummy)
        image_transforms.append(transform);full_K.append(np.linalg.inv(transform)@K[i])
    keep,support,consistency=view_consistency(depth,np.where(model_mask,confidence,np.nan),K,poses)
    args.output.mkdir(parents=True)
    arrays={'depth':depth,'confidence':confidence,'rgb':rgb,'intrinsics':K,'extrinsics':poses,
            'model_mask':model_mask,'full_intrinsics':np.array(full_K),'image_transforms':np.array(image_transforms)}
    np.savez_compressed(args.output/'predictions.npz',**arrays)
    np.savez_compressed(args.output/'consistency.npz',keep=keep,support=support)
    points=np.concatenate([unproject(d,k,e)[m] for d,k,e,m in zip(depth,K,poses,keep)])
    colors=np.concatenate([c[m] for c,m in zip(rgb,keep)])
    export_cloud(args.output/'depth-cloud.ply',points,colors)
    previews(args.output,depth,confidence,rgb,args.model)
    report={'model':args.model,'model_revision':revision,'source_revision':args.source_revision or json.loads((ROOT/'.cache/mapanything-source.json').read_text())['revision'],
            'mode':'joint','input_capture':args.capture.name,'image_names':names,'views':len(names),
            'resolution':list(depth.shape[1:]),'inference_seconds':round(elapsed,2),
            'peak_gpu_mb':round(torch.cuda.max_memory_allocated()/1024**2,1),'metric':False,
            'frame':'MapAnything predicted metric scale, unverified','weights_license':'Apache 2.0',
            'cloud_points':len(points),'consistency':consistency,
            'surface_mesh':export_surfaces(args.output,depth,rgb,K,poses,keep),
            'notes':[f'All {len(names)} selected input images used; no held-out accuracy claim.',
                     'Model scale is a prediction, not a physical calibration. Moving props and unseen areas remain uncertain.']}
    (args.output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'stage':'complete',**{k:report[k] for k in ('cloud_points','inference_seconds','peak_gpu_mb','surface_mesh')}}),flush=True)


if __name__=='__main__':main()
