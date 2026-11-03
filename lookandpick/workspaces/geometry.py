"""CPU-only geometry bridges used in stage subprocesses, never the HTTP process."""
import copy
import json
from pathlib import Path
import numpy as np
from .schema import canonical_frame,transform
from .repository import atomic_json


def edited_rectangle(surface,canonical_from_map,spacing):
    yaw=np.deg2rad(surface['yaw']);u=np.array([np.cos(yaw),np.sin(yaw),0.])
    v=np.array([-np.sin(yaw),np.cos(yaw),0.]) if surface['name']=='table' else np.array([0.,0.,1.])
    centre=np.array(surface['centre']);centre[2]=surface['height']
    width,depth=surface['width'],surface['depth']
    nx=min(320,max(2,int(width/spacing)+1));ny=min(320,max(2,int(depth/spacing)+1))
    a,b=np.meshgrid(np.linspace(-width/2,width/2,nx),np.linspace(-depth/2,depth/2,ny))
    world=centre+a[...,None]*u+b[...,None]*v
    frame=np.asarray(canonical_from_map);points=(world.reshape(-1,3)-frame[:3,3])@frame[:3,:3]
    n=frame[:3,:3].T@np.cross(u,v);origin=(centre-frame[:3,3])@frame[:3,:3]
    ids=np.arange(nx*ny).reshape(ny,nx);a,b,c,d=ids[:-1,:-1].ravel(),ids[:-1,1:].ravel(),ids[1:,:-1].ravel(),ids[1:,1:].ravel()
    faces=np.concatenate([np.stack([a,b,c],1),np.stack([b,d,c],1)])
    info={'origin':origin.tolist(),'axis_u':(frame[:3,:3].T@u).tolist(),'axis_v':(frame[:3,:3].T@v).tolist(),
          'footprint':[[-width/2,-depth/2],[width/2,-depth/2],[width/2,depth/2],[-width/2,depth/2]],'rectangle_assumption':True,'span':[width,depth]}
    return points.astype(np.float32),faces,info,n.astype(np.float32),float(-n@origin)


def review_assets(capture,geometry,masks,surfaces,output):
    """Small scene inspection data and mask overlays in the recovered map frame."""
    from PIL import Image
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    layout=json.loads((surfaces/'report.json').read_text());frame,table=canonical_frame(layout['planes'][0])
    entries=[table]
    for plane in layout['planes'][1:]:
        points=np.array([transform(frame,[plane['origin'][j]+p[0]*plane['axis_u'][j]+p[1]*plane['axis_v'][j] for j in range(3)]) for p in plane['footprint']])
        centre=points.mean(0);n=np.array(frame)[:3,:3]@plane['normal'];u=np.cross([0,0,1],n);u/=np.linalg.norm(u)
        x=points@u
        entries.append({'name':plane['name'],'enabled':True,'centre':centre.tolist(),'width':float(np.ptp(x)),'depth':float(np.ptp(points[:,2])),
                        'height':float(centre[2]),'yaw':float(np.rad2deg(np.arctan2(u[1],u[0])))})
    atomic_json(output/'layout.json',{'canonical_from_map':frame,'surfaces':entries})
    with np.load(surfaces/'observed-objects.npz',allow_pickle=False) as data:
        step=max(1,len(data['points'])//16000)
        atomic_json(output/'observed.json',{'points':data['points'][::step].tolist(),'colors':data['colors'][::step].tolist()})
    mask_overlays(capture,masks,output)


def mask_overlays(capture,masks,output):
    from PIL import Image
    manifest=json.loads((capture/'capture.json').read_text());palette={'table':[70,170,250],'wall':[170,110,240],'robot':[110,250,70],'tripod':[250,160,40],'chair':[240,80,160]}
    for image in manifest['images']:
        name=image['name']
        with Image.open(capture/'images'/name) as source:rgb=np.array(source.convert('RGB'))
        with np.load(masks/(Path(name).stem+'.npz'),allow_pickle=False) as data:
            for key,colour in palette.items():rgb[data[key]]=np.uint8(.52*rgb[data[key]]+.48*np.array(colour))
        Image.fromarray(rgb).save(output/(Path(name).stem+'-mask.jpg'),quality=90)


def apply_masks(directory,edits):
    from PIL import Image,ImageDraw
    for name,categories in edits.items():
        path=directory/(Path(name).stem+'.npz')
        with np.load(path,allow_pickle=False) as data:arrays={k:data[k] for k in data.files}
        height,width=next(iter(arrays.values())).shape
        for category,polygons in categories.items():
            image=Image.new('1',(width,height));draw=ImageDraw.Draw(image)
            for polygon in polygons:draw.polygon([(round(x*(width-1)),round(y*(height-1))) for x,y in polygon],fill=1)
            arrays[category]=np.array(image,dtype=bool)
        np.savez_compressed(path,**arrays)
    report=directory/'report.json'
    if report.exists():
        value=json.loads(report.read_text());value['mask_edits']=edits;value['notes']=[n for n in value.get('notes',[]) if 'No manual corrections' not in n]+['Manual polygon replacements applied; recorded in mask_edits.'];atomic_json(report,value)
