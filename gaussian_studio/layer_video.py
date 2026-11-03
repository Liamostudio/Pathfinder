"""Show the saved workspace layers being enabled individually in one scene."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from .depth_estimate import unproject
from .stage_video import camera_path,video_writer
from workspace_splat.formats import SH_C0


PHASES=[
    (1,'Empty workspace',[],False,'The camera stays fixed while the layers are introduced.'),
    (3,'1. Table surface',['table'],False,'Fitted rectangular tabletop, shown with a neutral material.'),
    (3,'2. Wall surface',['table','wall'],False,'The wall is a separate fitted surface.'),
    (4,'3. Photographic appearance',['table','wall','appearance'],False,'Learned colour is enabled on those same fixed surfaces.'),
    (3,'4. Observed equipment',['table','wall','appearance','props'],False,'Partial tripod and chair geometry is added independently.'),
    (3,'5. Observed robot',['table','wall','appearance','props','scan'],False,'The photo-derived robot is a separate, imperfect layer.'),
    (4,'6. Separate CAD robot',['table','wall','appearance','props','cad'],False,'The scanned robot is hidden; the known SO-101 CAD layer is enabled.'),
    (3,'7. Hide observed equipment',['table','wall','appearance','cad'],False,'The equipment layer is switched off without changing the background.'),
    (6,'8. Inspect the layered workspace',['table','wall','appearance','cad'],True,'A short camera sweep shows the final layer combination.'),
]


def phase_at(seconds):
    start=0
    for index,(duration,*_) in enumerate(PHASES):
        if seconds<start+duration:return index,start
        start+=duration
    return len(PHASES)-1,start-PHASES[-1][0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--trained',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output filename')
    root=args.run
    with np.load(root/'01-mapanything/predictions.npz',allow_pickle=False) as f:prediction={k:f[k] for k in f.files}
    with np.load(root/'03-surfaces-textured/surfaces.npz',allow_pickle=False) as f:surfaces={k:f[k] for k in f.files}
    with np.load(root/'03-surfaces-textured/masks.npz',allow_pickle=False) as f:masks={k:f[k] for k in f.files}
    with np.load(args.trained/'output/gaussians.npz',allow_pickle=False) as f:background={k:f[k] for k in f.files}
    scene=json.loads((args.trained/'output/scene.json').read_text())
    centre=np.array(scene['normalization']['centre']);radius=scene['normalization']['radius']
    if not np.allclose(background['means'],(surfaces['points']-centre)/radius,atol=1e-5):
        raise ValueError('Surface labels and trained Gaussian centres do not match')
    ids=surfaces['plane_ids'];table_id=0
    def cloud(points,colours):
        n=len(points);coefficients=np.zeros((n,4,3),np.float32);coefficients[:,0]=(colours-.5)/SH_C0
        return {'means':(points-centre)/radius,'scales':np.full((n,3),.0025),
                'quats':np.tile([1.,0,0,0],(n,1)),'opacities':np.full(n,.9),'sh_coefficients':coefficients}
    world=np.stack([unproject(d,k,e) for d,k,e in zip(prediction['depth'],prediction['intrinsics'],prediction['extrinsics'])])
    valid=prediction['model_mask']&np.isfinite(prediction['depth'])&(prediction['depth']>0)
    parts={}
    for key,mask in [('props',(masks['tripod']|masks['chair'])&~masks['robot']),('scan',masks['robot'])]:
        selected=valid&mask;parts[key]=cloud(world[selected],prediction['rgb'][selected]/255)
    with np.load(root/'09-urdf-layer/robot-points.npz',allow_pickle=False) as f:parts['cad']=cloud(f['points'],f['colors'])
    keys=('means','scales','quats','opacities','sh_coefficients')
    grey=np.zeros_like(background['sh_coefficients']);neutral=np.tile([.48,.61,.67],(len(ids),1))
    neutral[ids!=table_id]=[.68,.72,.73]
    light=np.array([.2,-.4,.9]);light/=np.linalg.norm(light)
    neutral*=.7+.3*np.abs(surfaces['normals']@light)[:,None]
    grey[:,0]=(neutral-.5)/SH_C0
    from workspace_splat.backend import configure_cuda
    configure_cuda()
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    def tensors_for(active):
        selected=((ids==table_id)&('table' in active))|((ids!=table_id)&('wall' in active))
        arrays={key:background[key][selected] for key in keys}
        if 'appearance' not in active:
            arrays['sh_coefficients']=grey[selected];arrays['opacities']=np.full(selected.sum(),.98)
        for name in ('props','scan','cad'):
            if name in active:arrays={key:np.concatenate([value,parts[name][key]]) for key,value in arrays.items()}
        result={key:torch.tensor(value,device='cuda',dtype=torch.float32).contiguous() for key,value in arrays.items()}
        result['quats']=F.normalize(result['quats'],dim=-1)
        return result
    fps=24;viewport_width,viewport_height=1200,900;panel_width=400;header,footer=60,40
    width,height=panel_width+viewport_width,header+viewport_height+footer
    duration=sum(p[0] for p in PHASES);frames=duration*fps
    path=camera_path(prediction,6*fps,viewport_width,viewport_height,order=[2,3,2])
    fonts={size:ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',size) for size in (16,18,20,24,28)}
    colours={'table':'#70bddd','wall':'#b2bac2','appearance':'#f0c16e','props':'#f7a653','scan':'#94d36f','cad':'#d5fd51'}
    rows=[('table','Table surface','Fitted rectangle'),('wall','Wall surface','Fitted plane'),
          ('appearance','Photo appearance','Learned Gaussian colour'),('props','Observed equipment','Partial photo geometry'),
          ('scan','Observed robot','Partial photo geometry'),('cad','SO-101 CAD robot','Known model; illustrative pose')]
    encoder=video_writer(args.output,width,height,fps);phase_index=-1;tensors=None
    try:
        for frame_index in range(frames):
            seconds=frame_index/fps;index,start=phase_at(seconds)
            phase=PHASES[index];active=phase[2]
            if index!=phase_index:
                phase_index=index;tensors=tensors_for(active)
                print(json.dumps({'phase':index,'title':phase[1],'active_layers':active}),flush=True)
            pose,K=path[min(len(path)-1,int((seconds-start)*fps))] if phase[3] else path[0]
            pose=pose.copy();pose[:3,3]=(pose[:3,:3]@centre+pose[:3,3])/radius
            if len(tensors['means']):
                with torch.inference_mode():
                    rgb,_,_=rasterization(tensors['means'],tensors['quats'],tensors['scales'],tensors['opacities'],tensors['sh_coefficients'],
                        torch.tensor(pose,device='cuda',dtype=torch.float32)[None],torch.tensor(K,device='cuda',dtype=torch.float32)[None],
                        viewport_width,viewport_height,sh_degree=1,packed=False,near_plane=.01,far_plane=100,
                        backgrounds=torch.tensor([[.035,.045,.055]],device='cuda'))
                    image=np.uint8(rgb[0].clamp(0,1).cpu().numpy()*255)
            else:image=np.full((viewport_height,viewport_width,3),[9,11,14],np.uint8)
            canvas=Image.new('RGB',(width,height),'#101719');canvas.paste(Image.fromarray(image),(panel_width,header))
            draw=ImageDraw.Draw(canvas)
            draw.text((22,16),'WORKSPACE / SPLIT LAYERS',fill='#e8eee1',font=fonts[24])
            draw.text((panel_width+22,16),phase[1],fill='#e8eee1',font=fonts[24])
            draw.line([(panel_width-1,0),(panel_width-1,height)],fill='#354247',width=2)
            draw.text((24,94),'LAYERS',fill='#8a9a9e',font=fonts[18])
            for row,(key,title,detail) in enumerate(rows):
                y=145+row*105;enabled=key in active
                draw.rounded_rectangle((24,y,376,y+86),radius=9,fill='#203132' if enabled else '#172023')
                draw.rounded_rectangle((42,y+22,64,y+44),radius=4,fill=colours[key] if enabled else '#344247')
                if enabled:draw.line([(47,y+32),(52,y+37),(60,y+27)],fill='#152024',width=3)
                draw.text((79,y+15),title,fill='#f0f4e9' if enabled else '#839094',font=fonts[20])
                draw.text((79,y+48),detail,fill='#a4b6b5' if enabled else '#657579',font=fonts[16])
            draw.text((24,825),'Relative scale',fill='#d8dfce',font=fonts[18])
            draw.text((24,857),'CAD placement and pose',fill='#92a4a7',font=fonts[16])
            draw.text((24,882),'are illustrative.',fill='#92a4a7',font=fonts[16])
            draw.text((panel_width+20,height-31),phase[4],fill='#b6c3c3',font=fonts[18])
            draw.rectangle((0,height-4,int(width*frame_index/(frames-1)),height),fill='#d5fd51')
            encoder.stdin.write(canvas.tobytes())
            if abs(seconds-start-.75)<1/(2*fps):canvas.save(args.output.with_name(args.output.stem+f'-phase-{index}.jpg'),quality=92)
        encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('Video encoder failed')
    except BaseException:
        encoder.kill();encoder.wait();args.output.unlink(missing_ok=True);raise
    args.output.with_suffix('.json').write_text(json.dumps({'seconds':duration,'fps':fps,'frames':frames,
        'size':[width,height],'phases':[{'duration':p[0],'title':p[1],'active_layers':p[2],'camera_sweep':p[3]} for p in PHASES],
        'notes':['Appearance is a material switch on the same fixed surfaces, not duplicate geometry.',
                 'Scanned robot is hidden when CAD robot is shown. CAD pose and placement are illustrative.',
                 'Geometry and CAD samples are rendered through the Gaussian point-kernel renderer.']},indent=2))
    print(json.dumps({'video':str(args.output),'seconds':duration,'frames':frames}),flush=True)


if __name__=='__main__':main()
