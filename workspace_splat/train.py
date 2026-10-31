"""Optimise anisotropic 3D Gaussians against calibrated multi-view photographs."""
from __future__ import annotations
import json
import math
from pathlib import Path
import time
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from .formats import export


def _train(capture, *, steps=2500, max_gaussians=40000, max_edge=640, seed=0, progress=lambda stage,info:None):
    from .backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy
    from gsplat.strategy.ops import remove
    if not torch.cuda.is_available():
        raise RuntimeError('Gaussian training requires an available CUDA GPU. Run outside the restricted sandbox or on the Legion GPU; reconstruction can still run on CPU.')
    if not 10<=steps<=30000 or not 100<=max_gaussians<=500000 or not 128<=max_edge<=1920:
        raise ValueError('Invalid training budget')
    torch.manual_seed(seed);np.random.seed(seed)
    capture=Path(capture);report=json.loads((capture/'reconstruction.json').read_text())
    initial=np.load(capture/'initial.npz',allow_pickle=False)
    means=initial['means'];colors=initial['colors']
    centre=np.median(means,axis=0);radius=float(np.quantile(np.linalg.norm(means-centre,axis=1),.9))
    if not math.isfinite(radius) or radius<=1e-8:raise ValueError('Degenerate reconstruction scale')
    means=(means-centre)/radius
    if len(means)>max_gaussians:
        indices=np.random.default_rng(seed).choice(len(means),max_gaussians,replace=False);means,colors=means[indices],colors[indices]
    distances=cKDTree(means).query(means,k=min(4,len(means)))[0][:,1:]
    scales=np.clip(np.mean(distances,axis=1),.001,.1)
    params=torch.nn.ParameterDict({
        'means':torch.nn.Parameter(torch.tensor(means,device='cuda')),
        'scales':torch.nn.Parameter(torch.tensor(np.log(scales[:,None].repeat(3,1)),device='cuda',dtype=torch.float32)),
        'quats':torch.nn.Parameter(torch.tensor(np.tile([1.,0.,0.,0.],(len(means),1)),device='cuda',dtype=torch.float32)),
        'opacities':torch.nn.Parameter(torch.full((len(means),),-1.4,device='cuda')),
        'colors':torch.nn.Parameter(torch.logit(torch.tensor(np.clip(colors,.001,.999),device='cuda'))),
    })
    rates={'means':.00016,'scales':.005,'quats':.001,'opacities':.05,'colors':.0025}
    optimizers={name:torch.optim.Adam([value],lr=rates[name],eps=1e-15) for name,value in params.items()}
    strategy=DefaultStrategy(refine_start_iter=100,refine_stop_iter=int(steps*.8),refine_every=100,reset_every=max(steps+1,3000),
                             grow_grad2d=.0002,prune_opa=.005,prune_scale3d=.5,verbose=False)
    strategy.check_sanity(params,optimizers);strategy_state=strategy.initialize_state(scene_scale=1.)
    cameras=[]
    for camera in report['cameras']:
        with Image.open(capture/'undistorted/images'/camera['name']) as im:
            w,h=im.size;factor=min(1,max_edge/max(w,h));w1,h1=round(w*factor),round(h*factor)
            rgb=np.array(im.convert('RGB').resize((w1,h1),Image.Resampling.LANCZOS)).astype(np.float32)/255
        k=np.array(camera['K'],dtype=np.float32);k[0]*=w1/w;k[1]*=h1/h
        view=np.array(camera['world_to_camera'],dtype=np.float32)
        view[:3,3]=(view[:3,:3]@centre+view[:3,3])/radius
        cameras.append({'name':camera['name'],'width':w1,'height':h1,'K':k.tolist(),'world_to_camera':view.tolist(),
                        'image':torch.tensor(rgb,device='cuda'),'k':torch.tensor(k,device='cuda')[None],
                        'view':torch.tensor(view,device='cuda')[None]})
    def render(camera):
        return rasterization(params['means'],F.normalize(params['quats'],dim=-1),torch.exp(params['scales']),
                             torch.sigmoid(params['opacities']),torch.sigmoid(params['colors']),camera['view'],camera['k'],
                             camera['width'],camera['height'],packed=False,near_plane=.01,far_plane=100,
                             backgrounds=torch.tensor([[.05,.06,.07]],device='cuda'))
    start=time.monotonic();history=[]
    rng=np.random.default_rng(seed)
    progress('training',{'step':0,'steps':steps,'gaussians':len(means),'message':'GPU renderer compiling/loading; first run can take a few minutes'})
    for step in range(1,steps+1):
        camera=cameras[int(rng.integers(len(cameras)))]
        rgb,alpha,info=render(camera)
        strategy.step_pre_backward(params,optimizers,strategy_state,step,info)
        # L1 + structural local contrast; fixed black boundary is included in reconstruction.
        target=camera['image'][None]
        a=rgb.permute(0,3,1,2);b=target.permute(0,3,1,2)
        mu_a=F.avg_pool2d(a,7,1,3);mu_b=F.avg_pool2d(b,7,1,3)
        var_a=F.avg_pool2d(a*a,7,1,3)-mu_a*mu_a
        var_b=F.avg_pool2d(b*b,7,1,3)-mu_b*mu_b
        covariance=F.avg_pool2d(a*b,7,1,3)-mu_a*mu_b
        ssim=((2*mu_a*mu_b+.01**2)*(2*covariance+.03**2)/((mu_a**2+mu_b**2+.01**2)*(var_a+var_b+.03**2))).mean()
        loss=.8*F.l1_loss(rgb,target)+.2*(1-ssim)+.001*torch.exp(params['scales']).mean()
        if not torch.isfinite(loss):raise RuntimeError('Training diverged; no new scene published')
        loss.backward()
        for optimizer in optimizers.values():optimizer.step();optimizer.zero_grad(set_to_none=True)
        strategy.step_post_backward(params,optimizers,strategy_state,step,info,packed=False)
        count=len(params['means'])
        if count>max_gaussians:
            score=torch.sigmoid(params['opacities']).detach()
            keep=torch.topk(score,max_gaussians).indices
            mask=torch.ones(count,device='cuda',dtype=torch.bool);mask[keep]=False
            remove(params,optimizers,strategy_state,mask)
        with torch.no_grad():
            params['scales'].clamp_(math.log(.0002),math.log(.3))
            params['means'].nan_to_num_(nan=0,posinf=10,neginf=-10)
        if step==1 or step%100==0 or step==steps:
            entry={'step':step,'steps':steps,'loss':float(loss.detach()),'gaussians':len(params['means']),
                   'elapsed_s':round(time.monotonic()-start,2),'gpu_memory_mb':round(torch.cuda.max_memory_allocated()/1024**2,1)}
            history.append(entry);progress('training',entry)
    progress('exporting',{'message':'Writing portable splat, 3DGS PLY and trained-view previews'})
    with torch.no_grad():
        values={k:v.detach().cpu().numpy() for k,v in params.items()}
        gaussians={'means':values['means'],'scales':np.exp(values['scales']),'quats':values['quats'],
                   'colors':1/(1+np.exp(-values['colors'])),'opacities':1/(1+np.exp(-values['opacities']))}
        output=capture/'output';output.mkdir(exist_ok=True)
        files=export(output,**gaussians)
        preview_dir=output/'previews';preview_dir.mkdir(exist_ok=True)
        metrics=[]
        for i,camera in enumerate(cameras):
            rgb,_,_=render(camera);prediction=rgb[0].clamp(0,1)
            mse=float(((prediction-camera['image'])**2).mean())
            metrics.append({'name':camera['name'],'psnr_db':-10*math.log10(max(mse,1e-10))})
            Image.fromarray((prediction.cpu().numpy()*255).astype('uint8')).save(preview_dir/f'{i:03d}.jpg',quality=93)
        np.savez_compressed(output/'checkpoint.npz',**values)
    bounds=np.quantile(gaussians['means'],[.02,.98],axis=0)
    public_cameras=[{k:v for k,v in camera.items() if k not in ('image','k','view')} for camera in cameras]
    scene={'version':1,'type':'gaussian-splat','name':capture.name,'format':'splat32','file':'scene.splat',**files,
           'trained':True,'steps':steps,'sh_degree':0,'frame':'normalised COLMAP; arbitrary scale','metric':False,
           'normalization':{'centre':centre.tolist(),'radius':radius},'bounds':bounds.tolist(),
           'cameras':public_cameras,'reconstruction':{k:v for k,v in report.items() if k!='cameras'},
           'training':{'backend':'gsplat CUDA','seed':seed,'history':history,'view_metrics':metrics,
                       'evaluation':'training views only; no held-out quality claim'},
           'warnings':report['warnings']+['Static workspace appearance only. The captured arm is baked into this scan. Geometry and scale are not validated for robot planning.']}
    temp=output/'scene.json.tmp';temp.write_text(json.dumps(scene,indent=2,allow_nan=False)+'\n');temp.replace(output/'scene.json')
    progress('complete',{'gaussians':files['count'],'message':'Trained Gaussian workspace ready','output':str(output)})
    return scene


def train(capture, **options):
    from filelock import FileLock, Timeout
    cache=Path(__file__).with_name('.cache');cache.mkdir(exist_ok=True)
    try:
        with FileLock(cache/'gpu-training.lock',timeout=0):
            return _train(capture,**options)
    except Timeout as exc:
        raise RuntimeError('Another workspace Gaussian training job already owns the GPU; wait for it to finish.') from exc
