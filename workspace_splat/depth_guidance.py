"""Camera-checked relative-depth supervision for experimental Gaussian fitting."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np


def sampling_grid(depth_K, image_K, depth_shape, image_shape):
    """Map depth-grid pixel centres into a rendered image (align_corners=True).

    Intrinsics encode resize AND crop. Resizing a depth map by image size alone
    is incorrect when the predictor centre-cropped its input.
    """
    height,width=depth_shape;image_height,image_width=image_shape
    y,x=np.indices((height,width),dtype=np.float32)
    pixels=np.stack([x,y,np.ones_like(x)],axis=-1)
    projected=pixels@(np.asarray(image_K)@np.linalg.inv(depth_K)).T
    xy=projected[...,:2]/projected[...,2:]
    inside=(xy[...,0]>=0)&(xy[...,0]<=image_width-1)&(xy[...,1]>=0)&(xy[...,1]<=image_height-1)
    grid=2*xy/np.array([image_width-1,image_height-1])-1
    return grid.astype(np.float32),inside


def load_guidance(directory,cameras,radius):
    """Require DA3 predictions explicitly aligned to these COLMAP cameras."""
    directory=Path(directory)
    report=json.loads((directory/'report.json').read_text())
    if report['mode']!='posed':raise ValueError('Depth guidance must be aligned to the input camera poses (posed mode)')
    with np.load(directory/'predictions.npz',allow_pickle=False) as saved:
        arrays={k:saved[k] for k in saved.files}
    with np.load(directory/'consistency.npz',allow_pickle=False) as saved:keep=saved['keep']
    if arrays['depth'].shape!=keep.shape:raise ValueError('Depth and consistency grids differ')
    by_name={name:i for i,name in enumerate(report['image_names'])}
    result={}
    for camera in cameras:
        if camera['name'] not in by_name:raise ValueError('Missing depth for '+camera['name'])
        i=by_name[camera['name']]
        if not np.allclose(arrays['extrinsics'][i,:3],np.asarray(camera['world_to_camera'])[:3],atol=1e-5,rtol=1e-5):
            raise ValueError('Depth camera frame does not match reconstruction')
        depth=arrays['depth'][i]/radius
        valid=keep[i]&np.isfinite(depth)&(depth>0)
        if not valid.any():raise ValueError('No supported depth for '+camera['name'])
        result[camera['name']]={'depth':depth.astype(np.float32),'keep':valid,'K':arrays['intrinsics'][i]}
    return result,{'model':report['model'],'model_revision':report.get('model_revision'),
                   'source_capture':report['input_capture'],'frame':report['frame'],
                   'weights_license':report.get('weights_license'),
                   'supported_pixels':int(keep.sum()),'metric':False}


def depth_loss(rendered_depth,alpha,target,mask,grid):
    """Robust log-depth error on supported pixels, plus an opacity coverage term."""
    import torch
    import torch.nn.functional as F
    sampled=F.grid_sample(torch.stack([rendered_depth,alpha],dim=0)[None],grid[None],
                          mode='bilinear',padding_mode='zeros',align_corners=True)[0]
    predicted,coverage=sampled
    visible=mask&(coverage.detach()>.1)&torch.isfinite(predicted)
    error=F.smooth_l1_loss(torch.log(predicted[visible].clamp_min(1e-4)),
                         torch.log(target[visible].clamp_min(1e-4)),beta=.1) if visible.any() else predicted.sum()*0
    return error,(1-coverage[mask]).mean()
