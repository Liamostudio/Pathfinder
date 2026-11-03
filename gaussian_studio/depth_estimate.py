"""Estimate joint multi-view depths and export inspectable geometry.

Run in the separate .depth-venv. Source photographs stay local. Saved depths
are raw relative estimates, never labelled as calibrated metres or Gaussians.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parent
DEFAULT_MODEL='depth-anything/DA3-LARGE-1.1'
DEFAULT_REVISION='0e109ae307c5982f319a67cf6f9f99ccdc0ec97c'


def unproject(depth,K,world_to_camera):
    """OpenCV optical-Z depths and world-to-camera pose -> world XYZ grid."""
    v,u=np.indices(depth.shape,dtype=np.float32)
    xyz=np.stack([(u-K[0,2])/K[0,0]*depth,(v-K[1,2])/K[1,1]*depth,depth],axis=-1)
    return (xyz-world_to_camera[:3,3])@world_to_camera[:3,:3]


def view_consistency(depth,confidence,intrinsics,extrinsics, *, relative_tolerance=.08,confidence_percentile=40):
    finite=np.isfinite(depth)&(depth>0)&np.isfinite(confidence)
    if not finite.any():raise ValueError('No finite positive depths with valid confidence')
    threshold=float(np.percentile(confidence[finite],confidence_percentile))
    valid=finite&(confidence>=threshold)
    support=np.zeros(depth.shape,dtype=np.uint8);pairs=[]
    for source in range(len(depth)):
        world=unproject(depth[source],intrinsics[source],extrinsics[source]).reshape(-1,3)
        source_ok=valid[source].ravel()
        for target in range(len(depth)):
            if source==target:continue
            pose=extrinsics[target];camera=world@pose[:3,:3].T+pose[:3,3]
            z=camera[:,2];K=intrinsics[target]
            with np.errstate(divide='ignore',invalid='ignore'):
                x=K[0,0]*camera[:,0]/z+K[0,2];y=K[1,1]*camera[:,1]/z+K[1,2]
            height,width=depth[target].shape
            inside=source_ok&np.isfinite(x)&np.isfinite(y)&(z>0)&(x>=0)&(x<width-.5)&(y>=0)&(y<height-.5)
            indices=np.flatnonzero(inside)
            px=np.rint(x[indices]).astype(int);py=np.rint(y[indices]).astype(int)
            usable=valid[target,py,px]
            indices,px,py=indices[usable],px[usable],py[usable]
            expected=depth[target,py,px]
            # Points behind a nearer surface are occluded, not evidence of bad depth.
            visible=z[indices]<=expected*(1+relative_tolerance)
            indices,expected=indices[visible],expected[visible]
            error=np.abs(z[indices]-expected)/np.maximum(expected,1e-8)
            supported=indices[error<=relative_tolerance]
            support[source].ravel()[supported]+=1
            pairs.append({'source':source,'target':target,'visible_comparisons':len(indices),
                          'median_relative_error':float(np.median(error)) if len(error) else None,
                          'agreement_fraction':float((error<=relative_tolerance).mean()) if len(error) else None})
    keep=valid&(support>=1)
    return keep,support,{'confidence_threshold':threshold,'relative_depth_tolerance':relative_tolerance,
                         'high_confidence_pixels':int(valid.sum()),'supported_pixels':int(keep.sum()),
                         'supported_fraction':float(keep.sum()/max(1,valid.sum())),
                         'note':'Cross-view agreement is an internal consistency check, not ground-truth accuracy.',
                         'pairs':pairs}


def export_cloud(path,points,colors):
    dtype=np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('red','u1'),('green','u1'),('blue','u1')])
    rows=np.empty(len(points),dtype=dtype)
    for axis,name in enumerate(('x','y','z')):rows[name]=points[:,axis]
    for channel,name in enumerate(('red','green','blue')):rows[name]=colors[:,channel]
    header=('ply\nformat binary_little_endian 1.0\ncomment Relative-depth point cloud, not a Gaussian splat\n'
            f'element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\n'
            'property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n')
    path.write_bytes(header.encode()+rows.tobytes())


def export_surfaces(output,depth,rgb,intrinsics,extrinsics,keep,stride=2):
    import trimesh
    vertices=[];faces=[];colors=[];offset=0
    for i in range(len(depth)):
        world=unproject(depth[i],intrinsics[i],extrinsics[i])[::stride,::stride]
        good=keep[i,::stride,::stride]
        d=depth[i,::stride,::stride]
        height,width=d.shape
        index=np.arange(height*width).reshape(height,width)
        a=index[:-1,:-1].ravel();b=index[:-1,1:].ravel();c=index[1:,:-1].ravel();e=index[1:,1:].ravel()
        triangles=np.concatenate([np.stack([a,c,b],axis=1),np.stack([b,c,e],axis=1)])
        depths=d.ravel()[triangles]
        connected=(depths.max(1)-depths.min(1))/np.maximum(depths.min(1),1e-8)<.06
        triangles=triangles[good.ravel()[triangles].all(1)&connected]
        vertices.append(world.reshape(-1,3));colors.append(rgb[i,::stride,::stride].reshape(-1,3))
        faces.append(triangles+offset);offset+=height*width
    mesh=trimesh.Trimesh(vertices=np.concatenate(vertices),faces=np.concatenate(faces),
                         vertex_colors=np.concatenate(colors),process=False)
    mesh.remove_unreferenced_vertices()
    if len(mesh.faces):mesh.export(output/'depth-surfaces.glb')
    return {'vertices':len(mesh.vertices),'triangles':len(mesh.faces),
            'note':'Partial, overlapping observed surfaces; not a watertight or calibrated collision mesh.'}


def previews(output,depth,confidence,rgb,model):
    import cv2
    positive=depth[np.isfinite(depth)&(depth>0)]
    low,high=np.percentile(positive,[2,98]);log_low,log_high=np.log([low,high])
    c_low,c_high=np.percentile(confidence[np.isfinite(confidence)],[2,98])
    width=480;height=round(width*depth.shape[1]/depth.shape[2]);gap=16
    sheet=Image.new('RGB',(width*3+gap*4,(height+gap)*len(depth)+112),'#f5f4ef')
    draw=ImageDraw.Draw(sheet)
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',16)
        heading=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',23)
    except OSError:font=heading=ImageFont.load_default()
    draw.text((gap,12),'Joint depth estimates | '+model.split('/')[-1],fill='#252823',font=heading)
    for column,title in enumerate(['Input photograph','Depth: warm = near, cool = far','Confidence: brighter = higher']):
        draw.text((gap+(width+gap)*column,49),title,fill='#53604a',font=font)
    for i in range(len(depth)):
        normalized=np.clip((np.log(np.maximum(depth[i],1e-8))-log_low)/(log_high-log_low),0,1)
        color=cv2.cvtColor(cv2.applyColorMap(np.uint8((1-normalized)*255),cv2.COLORMAP_TURBO),cv2.COLOR_BGR2RGB)
        color[~np.isfinite(depth[i])|(depth[i]<=0)]=[30,34,38]
        conf=np.uint8(np.clip((confidence[i]-c_low)/max(1e-8,c_high-c_low),0,1)*255)
        np.save(output/f'depth-{i:02d}.npy',depth[i]);np.save(output/f'confidence-{i:02d}.npy',confidence[i])
        Image.fromarray(color).save(output/f'depth-{i:02d}.png')
        Image.fromarray(conf).save(output/f'confidence-{i:02d}.png')
        for column,image in enumerate([rgb[i],color,np.repeat(conf[...,None],3,axis=2)]):
            sheet.paste(Image.fromarray(image).resize((width,height),Image.Resampling.LANCZOS),
                        (gap+column*(width+gap),82+i*(height+gap)))
    draw.text((gap,sheet.height-23),'All views share one relative-depth colour scale. Distances have not been calibrated in metres.',fill='#74776c',font=font)
    sheet.save(output/'depth-comparison.jpg',quality=94)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--mode',choices=['joint','posed'],default='joint')
    parser.add_argument('--model',default=DEFAULT_MODEL)
    parser.add_argument('--revision',help='Hugging Face model commit; the default model uses a pinned revision')
    parser.add_argument('--resolution',type=int,default=504)
    parser.add_argument('--postprocess-only',action='store_true')
    args=parser.parse_args();output=args.output
    if not args.postprocess_only and output.exists() and any(output.iterdir()):parser.error('Output must be new or empty')
    if not 224<=args.resolution<=1008:parser.error('Choose resolution between 224 and 1008')
    output.mkdir(parents=True,exist_ok=True)
    if args.postprocess_only:
        with np.load(output/'predictions.npz',allow_pickle=False) as result:
            arrays={key:result[key] for key in result.files}
        report=json.loads((output/'report.json').read_text())
    else:
        import torch
        from depth_anything_3.api import DepthAnything3
        if not torch.cuda.is_available():raise SystemExit('This experiment requires CUDA')
        torch.set_num_threads(8)
        manifest=json.loads((args.capture/'capture.json').read_text())
        names=[image['name'] for image in manifest['images']]
        poses=intrinsics=None
        if args.mode=='posed':
            reconstructed=json.loads((args.capture/'reconstruction.json').read_text())
            by_name={camera['name']:camera for camera in reconstructed['cameras']}
            names=[name for name in names if name in by_name]
            poses=np.asarray([by_name[name]['world_to_camera'] for name in names],dtype=np.float32)
            intrinsics=np.asarray([by_name[name]['K'] for name in names],dtype=np.float32)
            images=[str(args.capture/'undistorted/images'/name) for name in names]
        else:images=[str(args.capture/'images'/name) for name in names]
        print(json.dumps({'stage':'loading_model','model':args.model,'mode':args.mode,'views':len(images)}),flush=True)
        revision=args.revision or (DEFAULT_REVISION if args.model==DEFAULT_MODEL else None)
        model=DepthAnything3.from_pretrained(args.model,revision=revision,cache_dir=str(ROOT/'.cache/huggingface')).to('cuda').eval()
        print(json.dumps({'stage':'inference','resolution':args.resolution}),flush=True)
        torch.cuda.reset_peak_memory_stats();start=time.monotonic()
        result=model.inference(images,extrinsics=poses,intrinsics=intrinsics,process_res=args.resolution,infer_gs=False)
        torch.cuda.synchronize()
        arrays={'depth':result.depth,'confidence':result.conf,'rgb':result.processed_images,
                'intrinsics':result.intrinsics,'extrinsics':result.extrinsics}
        if any(value is None or not np.isfinite(value).all() for value in arrays.values()):raise RuntimeError('Model returned missing or non-finite predictions')
        np.savez_compressed(output/'predictions.npz',**arrays)
        report={'model':args.model,'model_revision':revision or getattr(model,'_commit_hash',None),'mode':args.mode,'input_capture':args.capture.name,
                'image_names':names,'views':len(images),'resolution':list(result.depth.shape[1:]),
                'inference_seconds':round(time.monotonic()-start,2),'peak_gpu_mb':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'metric':False,'frame':'COLMAP arbitrary scale' if args.mode=='posed' else 'DA3 predicted cameras, arbitrary scale',
                'weights_license':'CC BY-NC 4.0' if 'LARGE' in args.model or 'GIANT' in args.model else 'Apache 2.0',
                'source_url':'https://github.com/ByteDance-Seed/Depth-Anything-3',
                'notes':[f'Estimated geometry from {len(images)} source views, not measured ground truth.',
                         'Hidden surfaces are not recovered; metric calibration and robot-frame alignment are still required.']}
        (output/'report.json').write_text(json.dumps(report,indent=2))
        del result,model;torch.cuda.empty_cache()
    depth,confidence,rgb,K,poses=[arrays[name] for name in ['depth','confidence','rgb','intrinsics','extrinsics']]
    print(json.dumps({'stage':'cross_view_validation'}),flush=True)
    keep,support,consistency=view_consistency(depth,confidence,K,poses)
    report['consistency']=consistency
    np.savez_compressed(output/'consistency.npz',keep=keep,support=support)
    points=np.concatenate([unproject(depth[i],K[i],poses[i])[keep[i]] for i in range(len(depth))])
    colors=np.concatenate([rgb[i][keep[i]] for i in range(len(depth))])
    export_cloud(output/'depth-cloud.ply',points,colors)
    report['cloud_points']=len(points)
    previews(output,depth,confidence,rgb,report['model'])
    report['surface_mesh']=export_surfaces(output,depth,rgb,K,poses,keep)
    (output/'cameras.json').write_text(json.dumps({'names':report['image_names'],'intrinsics':K.tolist(),
                                                 'world_to_camera':poses.tolist(),'metric':False},indent=2))
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'stage':'complete','output':str(output),'cloud_points':len(points),
                      'supported_fraction':consistency['supported_fraction'],'triangles':report['surface_mesh']['triangles']}),flush=True)


if __name__=='__main__':main()
