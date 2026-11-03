"""Create a separate, depth-guided version of a trained local capture.

Run with the Gaussian CUDA runtime, not the depth-model environment.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import time
import numpy as np
from .depth_estimate import unproject
from workspace_splat.depth_guidance import load_guidance


def prepare(source,depth_directory,destination,seed_points=60000):
    source,depth_directory,destination=map(Path,(source,depth_directory,destination))
    if destination.exists():raise ValueError('Choose a new destination capture; existing results are preserved')
    baseline=json.loads((source/'output/scene.json').read_text())
    reconstruction=json.loads((source/'reconstruction.json').read_text())
    normalization=baseline['normalization'];radius=normalization['radius']
    load_guidance(depth_directory,reconstruction['cameras'],radius)
    with np.load(depth_directory/'predictions.npz',allow_pickle=False) as saved:
        arrays={k:saved[k] for k in saved.files}
    with np.load(depth_directory/'consistency.npz',allow_pickle=False) as saved:keep=saved['keep']
    points=np.concatenate([unproject(d,k,e)[m] for d,k,e,m in zip(arrays['depth'],arrays['intrinsics'],arrays['extrinsics'],keep)])
    colors=np.concatenate([rgb[m] for rgb,m in zip(arrays['rgb'],keep)]).astype(np.float32)/255
    # Select spatially distributed samples rather than duplicating overlapping pixels.
    order=np.random.default_rng(0).permutation(len(points))
    cells=np.floor(points[order]/(radius*.005)).astype(np.int64)
    _,unique=np.unique(cells,axis=0,return_index=True)
    selected=order[unique]
    if len(selected)>seed_points:selected=np.random.default_rng(0).choice(selected,seed_points,replace=False)
    with np.load(source/'initial.npz',allow_pickle=False) as sparse:
        means=np.concatenate([sparse['means'],points[selected]]).astype(np.float32)
        colors=np.concatenate([sparse['colors'],colors[selected]]).astype(np.float32)
    destination.mkdir(parents=True)
    for name in ('capture.json','reconstruction.json'):shutil.copy2(source/name,destination/name)
    for name in ('images','undistorted/images'):shutil.copytree(source/name,destination/name)
    np.savez_compressed(destination/'initial.npz',means=means,colors=colors,
                        normalization_centre=np.array(normalization['centre'],dtype=np.float32),
                        normalization_radius=np.float32(radius))
    experiment={'baseline_capture':source.name,'depth_source':str(depth_directory.resolve()),
                'depth_seed_points':len(selected),'sparse_seed_points':len(means)-len(selected),
                'comparison':'Same images, COLMAP cameras, normalization, seed, steps, Gaussian cap and RGB resolution; dense initialization plus depth loss added.',
                'limitations':['Training-view RGB and agreement with the guiding depths are not held-out accuracy.',
                               'Only four views; unseen surfaces and moving objects remain unresolved.']}
    (destination/'experiment.json').write_text(json.dumps(experiment,indent=2))
    return baseline,experiment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--depth',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',type=int,help='Defaults to the baseline training budget')
    parser.add_argument('--depth-weight',type=float,default=.15)
    args=parser.parse_args()
    baseline,experiment=prepare(args.capture,args.depth,args.output)
    def progress(stage,info):
        payload={'stage':stage,'updated_at':time.time(),**info}
        temp=args.output/'status.json.tmp';temp.write_text(json.dumps(payload,indent=2));temp.replace(args.output/'status.json')
        if stage!='training' or info.get('step',0)%1000==0:print(json.dumps(payload),flush=True)
    print(json.dumps(experiment),flush=True)
    from workspace_splat.train import train
    try:
        train(args.output,steps=args.steps or baseline['steps'],max_gaussians=baseline['training']['max_gaussians'],
              max_edge=baseline['training']['max_edge'],seed=baseline['training']['seed'],
              depth_guidance=args.depth,depth_weight=args.depth_weight,progress=progress)
    except Exception as error:
        progress('failed',{'error':str(error)});raise


if __name__=='__main__':main()
