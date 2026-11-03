"""Fit Gaussian appearance on fixed MapAnything/SAM planar workspace geometry."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import shutil
import time
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from workspace_splat.formats import export,SH_C0


def normal_quaternions(normals):
    """Orient local Z along the surface normal; return 3DGS w,x,y,z."""
    n=normals/np.linalg.norm(normals,axis=1,keepdims=True)
    reference=np.tile([0.,0.,1.],(len(n),1));reference[np.abs(n[:,2])>.9]=[0,1,0]
    x=np.cross(reference,n);x/=np.linalg.norm(x,axis=1,keepdims=True);y=np.cross(n,x)
    rotations=np.stack([x,y,n],axis=-1)
    return Rotation.from_matrix(rotations).as_quat()[:,[3,0,1,2]].astype(np.float32)


def export_full_sh(path,values,coefficients):
    """Write standard 3DGS PLY, retaining channel-major higher-order SH."""
    rest=coefficients[:,1:].transpose(0,2,1).reshape(len(coefficients),-1)
    props=['x','y','z','nx','ny','nz','f_dc_0','f_dc_1','f_dc_2']
    props += [f'f_rest_{i}' for i in range(rest.shape[1])]
    props += ['opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    opacity=np.clip(values['opacities'],1e-6,1-1e-6)
    rows=np.column_stack([values['means'],np.zeros_like(values['means']),coefficients[:,0],rest,
        np.log(opacity/(1-opacity)),np.log(values['scales']),values['quats']]).astype('<f4')
    if not np.isfinite(rows).all():raise ValueError('Invalid Gaussian SH export')
    header=('ply\nformat binary_little_endian 1.0\ncomment Surface-constrained Gaussian appearance with full SH\n'
        f'element vertex {len(rows)}\n'+''.join('property float '+p+'\n' for p in props)+'end_header\n')
    path.write_bytes(header.encode()+rows.tobytes())
    return path.stat().st_size


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--surfaces',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',type=int,default=8000);parser.add_argument('--max-edge',type=int,default=1280)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new capture directory')
    if not 10<=args.steps<=20000:parser.error('Choose 10 to 20,000 steps')
    with np.load(args.geometry/'predictions.npz',allow_pickle=False) as saved:prediction={k:saved[k] for k in saved.files}
    with np.load(args.surfaces/'surfaces.npz',allow_pickle=False) as saved:surface={k:saved[k] for k in saved.files}
    with np.load(args.surfaces/'masks.npz',allow_pickle=False) as saved:masks={k:saved[k] for k in saved.files}
    report=json.loads((args.geometry/'report.json').read_text());layout=json.loads((args.surfaces/'report.json').read_text())
    from workspace_splat.backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    from workspace_splat.depth_guidance import sampling_grid
    torch.manual_seed(0);torch.set_num_threads(8)
    centre=np.array(layout['normalization']['centre']);radius=layout['normalization']['radius']
    points=(surface['points']-centre)/radius
    colour=surface['colors'];normals=surface['normals']
    # Fixed centres and normals prevent the fitter inventing floating geometry.
    means=torch.tensor(points,device='cuda',dtype=torch.float32)
    quats=torch.tensor(normal_quaternions(normals),device='cuda')
    spacing=np.median(cKDTree(points).query(points,k=2)[0][:,1])
    base_scale=float(max(spacing*1.05,.0015))
    initial_sh=np.zeros((len(points),4,3),np.float32);initial_sh[:,0]=(colour-.5)/SH_C0
    sh=torch.nn.Parameter(torch.tensor(initial_sh,device='cuda'))
    opacity=torch.nn.Parameter(torch.full((len(points),),math.log(.9/.1),device='cuda'))
    planar_scale=torch.nn.Parameter(torch.full((len(points),2),math.log(base_scale),device='cuda'))
    initial_sh=torch.tensor(initial_sh,device='cuda')
    optimizer=torch.optim.Adam([{'params':[sh],'lr':.0025},{'params':[opacity],'lr':.005},
                                {'params':[planar_scale],'lr':.001}],eps=1e-15)
    cameras=[]
    static=(masks.get('table_texture',masks['table'])|masks['wall'])&~masks['excluded']
    args.output.mkdir(parents=True);shutil.copy2(args.source/'capture.json',args.output/'capture.json')
    shutil.copytree(args.source/'images',args.output/'images');shutil.copytree(args.source/'images',args.output/'undistorted/images')
    def scales():return torch.cat([planar_scale.exp(),torch.full((len(points),1),base_scale*.12,device='cuda')],dim=1)
    def render(camera):
        return rasterization(means,quats,scales(),opacity.sigmoid(),sh,camera['view'],camera['k'],
            camera['width'],camera['height'],packed=False,sh_degree=1,near_plane=.01,far_plane=100,
            backgrounds=torch.tensor([[.04,.05,.06]],device='cuda'))
    for i,name in enumerate(report['image_names']):
        with Image.open(args.source/'images'/name) as image:
            width,height=image.size;factor=min(1,args.max_edge/max(width,height));w,h=round(width*factor),round(height*factor)
            target=np.array(image.convert('RGB').resize((w,h),Image.Resampling.LANCZOS))/255
        K=prediction['full_intrinsics'][i].copy();K[0]*=w/width;K[1]*=h/height
        pose=prediction['extrinsics'][i].copy();pose[:3,3]=(pose[:3,:3]@centre+pose[:3,3])/radius
        # Warp semantic masks with calibrated rays, not size-only resizing.
        grid,inside=sampling_grid(K,prediction['intrinsics'][i],(h,w),static[i].shape)
        mask=F.grid_sample(torch.tensor(static[i],device='cuda',dtype=torch.float32)[None,None],
            torch.tensor(grid,device='cuda')[None],mode='nearest',align_corners=True)[0,0]>.5
        camera={'name':name,'width':w,'height':h,'K':K.tolist(),'world_to_camera':pose.tolist(),
                'view':torch.tensor(pose,device='cuda')[None],'k':torch.tensor(K,device='cuda',dtype=torch.float32)[None],
                'target':torch.tensor(target,device='cuda',dtype=torch.float32)[None]}
        with torch.no_grad():_,alpha,_=render(camera)
        camera['mask']=mask&torch.tensor(inside,device='cuda')&(alpha[0,...,0]>.5)
        if camera['mask'].sum()<1000:raise RuntimeError('Insufficient covered static workspace pixels')
        cameras.append(camera)
    public_cameras=[{k:v for k,v in c.items() if k not in ('view','k','target','mask')} for c in cameras]
    reconstruction={'registered':len(cameras),'total':len(cameras),'points':len(points),'cameras':public_cameras,
                    'metric':False,'frame':report['frame'],'warnings':['Model-derived geometry, relative scale. Only the table and wall are fitted.']}
    (args.output/'reconstruction.json').write_text(json.dumps(reconstruction,indent=2))
    normalization=layout['normalization'];history=[];started=time.monotonic();rng=np.random.default_rng(0)
    def save(step):
        with torch.no_grad():
            coefficients=sh.cpu().numpy();values={'means':means.cpu().numpy(),'quats':quats.cpu().numpy(),
                'scales':scales().cpu().numpy(),'opacities':opacity.sigmoid().cpu().numpy(),
                'colors':np.clip(coefficients[:,0]*SH_C0+.5,0,1)}
            output=args.output/'output';files=export(output,**values)
            files['ply_bytes']=export_full_sh(output/'scene.ply',values,coefficients)
            np.savez_compressed(output/'gaussians.npz',**values,sh_coefficients=coefficients)
            view_metrics=[]
            previews=output/'previews';previews.mkdir(exist_ok=True)
            for index,camera in enumerate(cameras):
                rgb,_,_=render(camera);mask=camera['mask'];mse=float(((rgb[0][mask]-camera['target'][0][mask])**2).mean())
                view_metrics.append({'name':camera['name'],'masked_training_psnr_db':-10*math.log10(max(mse,1e-10)),
                                     'evaluated_pixel_fraction':float(mask.float().mean())})
                Image.fromarray(np.uint8(rgb[0].clamp(0,1).cpu().numpy()*255)).save(previews/f'{index:03d}.jpg',quality=94)
            scene={'version':1,'type':'gaussian-splat','name':args.output.name,'format':'splat32','file':'scene.splat',**files,
                'trained':True,'steps':step,'sh_degree':0,'ply_sh_degree':1,'normalization':normalization,'bounds':np.quantile(points,[.02,.98],axis=0).tolist(),
                'frame':report['frame'],'metric':False,'cameras':public_cameras,'reconstruction':reconstruction,
                'training':{'backend':'gsplat CUDA, fixed planar surfaces','seed':0,'source_sh_degree':1,'history':history,
                    'max_edge':args.max_edge,'max_gaussians':len(points),
                    'view_metrics':view_metrics,'geometry':'Fixed centres and normals; bounded in-plane scales; learned opacity and SH colour.',
                    'evaluation':'Only covered static table/wall training pixels. Cannot compare directly with previous whole-image PSNR.'},
                'warnings':['Table/wall boundaries are fitted assumptions; physical calibration pending.',
                    'Native SPLAT viewer shows SH degree zero; PLY and stage video retain full degree-one colour.',
                    'Background only. Robot/tripods are separate observed object layers; no hidden object completion.']}
            (output/'scene.json').write_text(json.dumps(scene,indent=2))
    try:
        for step in range(1,args.steps+1):
            camera=cameras[int(rng.integers(len(cameras)))];rgb,alpha,_=render(camera);mask=camera['mask']
            colour_loss=F.l1_loss(rgb[0][mask],camera['target'][0][mask])
            regularization=.002*(sh[:,1:]**2).mean()+.001*((sh[:,0]-initial_sh[:,0])**2).mean()
            loss=colour_loss+regularization+.01*(1-alpha[0,...,0][mask]).mean()
            if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
            loss.backward();optimizer.step();optimizer.zero_grad(set_to_none=True)
            with torch.no_grad():planar_scale.clamp_(math.log(base_scale*.75),math.log(base_scale*2.))
            if step==1 or step%100==0 or step==args.steps:
                entry={'step':step,'steps':args.steps,'loss':float(loss.detach()),'elapsed_s':round(time.monotonic()-started,2)}
                history.append(entry)
                (args.output/'status.json').write_text(json.dumps({'stage':'training','gaussians':len(points),**entry}))
                if step==1 or step%1000==0:print(json.dumps(entry),flush=True)
            if step in (1000,args.steps):
                save(step)
                if step<args.steps:shutil.copytree(args.output/'output',args.output/f'checkpoint-{step}'/'output')
        (args.output/'status.json').write_text(json.dumps({'stage':'complete','steps':args.steps,'gaussians':len(points)}))
        print(json.dumps({'stage':'complete','output':str(args.output),'seconds':round(time.monotonic()-started,2)}),flush=True)
    except BaseException:
        (args.output/'status.json').write_text(json.dumps({'stage':'failed'}));raise


if __name__=='__main__':main()
