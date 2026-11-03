"""Fit inspectable planar workspace surfaces and retain separate object layers."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from .depth_estimate import unproject,export_cloud


def fit_plane(points,tolerance,up=None,vertical=False,seed=0):
    if len(points)<100:raise ValueError('Not enough points to fit a surface')
    rng=np.random.default_rng(seed);sample=points[rng.choice(len(points),min(5000,len(points)),replace=False)]
    triples=sample[rng.integers(len(sample),size=(1200,3))]
    normals=np.cross(triples[:,1]-triples[:,0],triples[:,2]-triples[:,0])
    lengths=np.linalg.norm(normals,axis=1);valid=lengths>1e-8
    normals=normals[valid]/lengths[valid,None];offsets=-(normals*triples[valid,0]).sum(1)
    if up is not None:
        cosine=np.abs(normals@up)
        valid=cosine<.45 if vertical else cosine>.65
        normals,offsets=normals[valid],offsets[valid]
    if not len(normals):raise ValueError('No plane compatible with the orientation prior')
    best=None;best_count=0
    for start in range(0,len(normals),64):
        errors=np.abs(sample@normals[start:start+64].T+offsets[None,start:start+64])
        counts=(errors<tolerance).sum(0);index=int(counts.argmax())
        if counts[index]>best_count:best_count=counts[index];best=(normals[start+index],offsets[start+index])
    normal,offset=best
    for _ in range(3):
        inliers=np.abs(points@normal+offset)<tolerance
        if inliers.sum()<100:raise ValueError('Surface fit has too little support')
        selected=points[inliers];centre=selected.mean(0)
        _,_,vh=np.linalg.svd((selected-centre)[::max(1,len(selected)//20000)],full_matrices=False)
        normal=vh[-1];offset=-normal@centre
    if up is not None and normal@up<0:normal,offset=-normal,-offset
    inliers=np.abs(points@normal+offset)<tolerance
    return normal.astype(np.float32),float(offset),inliers


def plane_grid(points,normal,offset,spacing,rectangle=False):
    import cv2
    origin=points.mean(0);origin=origin-(origin@normal+offset)*normal
    _,_,vh=np.linalg.svd(points[::max(1,len(points)//10000)]-origin,full_matrices=False)
    u=vh[0]-normal*(vh[0]@normal);u/=np.linalg.norm(u);v=np.cross(normal,u)
    xy=np.stack([(points-origin)@u,(points-origin)@v],axis=1).astype(np.float32)
    # Trim extreme outliers before estimating the visible footprint.
    low,high=np.percentile(xy,[1,99],axis=0);xy=xy[((xy>=low)&(xy<=high)).all(1)]
    hull=cv2.boxPoints(cv2.minAreaRect(xy)) if rectangle else cv2.convexHull(xy).reshape(-1,2)
    low,high=hull.min(0),hull.max(0)
    width=max(2,min(320,int(np.ceil((high[0]-low[0])/spacing))+1))
    height=max(2,min(320,int(np.ceil((high[1]-low[1])/spacing))+1))
    x,y=np.meshgrid(np.linspace(low[0],high[0],width),np.linspace(low[1],high[1],height))
    # A rasterised convex footprint supplies deterministic regular samples and triangles.
    polygon=np.rint((hull-low)/np.maximum(high-low,1e-8)*[width-1,height-1]).astype(np.int32)
    valid=np.zeros((height,width),np.uint8);cv2.fillConvexPoly(valid,polygon,1)
    points=origin+x[...,None]*u+y[...,None]*v
    ids=np.arange(width*height).reshape(height,width)
    a,b,c,d=ids[:-1,:-1].ravel(),ids[:-1,1:].ravel(),ids[1:,:-1].ravel(),ids[1:,1:].ravel()
    triangles=np.concatenate([np.stack([a,b,c],axis=1),np.stack([b,d,c],axis=1)])
    triangles=triangles[valid.ravel()[triangles].all(1)]
    used=np.flatnonzero(valid.ravel());remap=np.full(width*height,-1);remap[used]=np.arange(len(used))
    return points.reshape(-1,3)[used].astype(np.float32),remap[triangles],{
        'origin':origin.tolist(),'axis_u':u.tolist(),'axis_v':v.tolist(),'footprint':hull.tolist(),
        'rectangle_assumption':rectangle,'span':(high-low).tolist()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry',required=True,type=Path);parser.add_argument('--masks',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--reference-view',default='legacy',help='auto or selected image name; legacy keeps the prototype default')
    parser.add_argument('--overrides',type=Path,help='Validated canonical frame and editable surface rectangles')
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    import cv2
    import trimesh
    with np.load(args.geometry/'predictions.npz',allow_pickle=False) as f:data={k:f[k] for k in f.files}
    with np.load(args.geometry/'consistency.npz',allow_pickle=False) as f:keep=f['keep']
    report=json.loads((args.geometry/'report.json').read_text());height,width=data['depth'].shape[1:]
    masks={key:[] for key in ('table','wall','robot','tripod','chair')};full_masks=[]
    for name,A in zip(report['image_names'],data['image_transforms']):
        with np.load(args.masks/(Path(name).stem+'.npz'),allow_pickle=False) as f:
            original={key:f[key] for key in masks}
            full_masks.append(original)
            for key in masks:masks[key].append(cv2.warpPerspective(original[key].astype(np.uint8),A,(width,height),flags=cv2.INTER_NEAREST)>0)
    masks={key:np.stack(value) for key,value in masks.items()}
    from scipy.ndimage import binary_fill_holes
    # SAM excludes paper/markers as holes in the tabletop mask. Preserve their
    # observed texture; robot and equipment masks are still subtracted below.
    masks['table_texture']=np.stack([binary_fill_holes(mask) for mask in masks['table']])
    objects=masks['robot']|masks['tripod']|masks['chair']
    excluded=np.stack([cv2.dilate(m.astype(np.uint8),np.ones((5,5),np.uint8))>0 for m in objects])
    world=np.stack([unproject(d,k,e) for d,k,e in zip(data['depth'],data['intrinsics'],data['extrinsics'])])
    all_points=world[keep];centre=np.median(all_points,axis=0);radius=float(np.percentile(np.linalg.norm(all_points-centre,axis=1),90))
    up=-data['extrinsics'][:,1,:3].mean(0);up/=np.linalg.norm(up)
    plane_inputs={key:world[keep&masks[key]&~excluded] for key in ('table','wall')}
    normal,offset,inliers=fit_plane(plane_inputs['table'],radius*.018,up=up)
    if inliers.mean()<.35:raise RuntimeError('Table estimates disagree too strongly for a reliable plane proxy')
    planes=[('table',normal,offset,plane_inputs['table'][inliers],float(inliers.mean()))]
    remaining=plane_inputs['wall']
    for i in range(3):
        if len(remaining)<2000:break
        n,d,inliers=fit_plane(remaining,radius*.015,up=normal,vertical=True,seed=i+1)
        if inliers.sum()<2000 or inliers.mean()<.12:break
        planes.append((f'wall-{i+1}',n,d,remaining[inliers],float(inliers.mean())))
        # Remove the neighbourhood too, avoiding duplicate almost-parallel walls
        # when a noisy reconstruction produces several nearby depth sheets.
        remaining=remaining[np.abs(remaining@n+d)>radius*.07]
    overrides=json.loads(args.overrides.read_text()) if args.overrides else None
    reference=2
    if args.reference_view=='auto':
        scores=[float(np.where(masks['table_texture'][i]&~excluded[i],data['confidence'][i],0).sum()) for i in range(len(keep))]
        reference=int(np.argmax(scores))
    elif args.reference_view!='legacy':reference=report['image_names'].index(args.reference_view)
    def texture(points,kind):
        colours=np.zeros((len(points),3),np.float32);best=np.full(len(points),-1.);source=np.full(len(points),-1)
        for i,(pose,K,rgb) in enumerate(zip(data['extrinsics'],data['intrinsics'],data['rgb'])):
            camera=points@pose[:3,:3].T+pose[:3,3];z=camera[:,2]
            with np.errstate(divide='ignore',invalid='ignore'):
                px=np.rint(camera[:,0]/z*K[0,0]+K[0,2]);py=np.rint(camera[:,1]/z*K[1,1]+K[1,2])
            inside=(z>0)&np.isfinite(px)&np.isfinite(py)&(px>=0)&(px<width)&(py>=0)&(py<height)
            index=np.flatnonzero(inside);x,y=px[index].astype(int),py[index].astype(int)
            texture_mask=masks['table_texture'] if kind=='table' else masks['wall']
            visible=~excluded[i,y,x]&texture_mask[i,y,x]
            index,x,y=index[visible],x[visible],y[visible]
            expected=data['depth'][i,y,x]
            relative=np.abs(z[index]-expected)/np.maximum(z[index],1e-6)
            score=data['confidence'][i,y,x]/(1+relative*30)
            # One primary table texture avoids averaging relocated checkerboards across views.
            if kind=='table' and i==reference:score+=100
            use=score>best[index];index,x,y,score=index[use],x[use],y[use],score[use]
            colours[index]=rgb[y,x]/255;best[index]=score;source[index]=i
        valid=source>=0
        if valid.any() and not valid.all():
            from scipy.spatial import cKDTree
            nearest=cKDTree(points[valid]).query(points[~valid])[1]
            colours[~valid]=colours[valid][nearest]
        return colours,valid,source
    args.output.mkdir(parents=True);vertices=[];faces=[];colours=[];normals=[];labels=[];plane_reports=[];offset_index=0
    for index,(name,n,d,observed,fraction) in enumerate(planes):
        if overrides:
            from lookandpick.workspaces.geometry import edited_rectangle
            edit=next((s for s in overrides['surfaces'] if s['name']==name),None)
            if edit and not edit['enabled']:continue
            if edit:points,triangles,footprint,n,d=edited_rectangle(edit,overrides['canonical_from_map'],radius*.004)
            else:points,triangles,footprint=plane_grid(observed,n,d,radius*.004,rectangle=name=='table')
        else:points,triangles,footprint=plane_grid(observed,n,d,radius*.004,rectangle=name=='table')
        rgb,visible,source=texture(points,'table' if name=='table' else 'wall')
        vertices.append(points);faces.append(triangles+offset_index);colours.append(rgb);normals.append(np.tile(n,(len(points),1)));labels.append(np.full(len(points),index))
        offset_index+=len(points)
        plane_reports.append({'name':name,'normal':n.tolist(),'offset':d,'inlier_fraction':fraction,
                              'observed_samples':len(observed),'surface_samples':len(points),'texture_visible_fraction':float(visible.mean()),**footprint})
    points=np.concatenate(vertices);rgb=np.concatenate(colours);surface_normals=np.concatenate(normals);plane_ids=np.concatenate(labels)
    mesh=trimesh.Trimesh(vertices=points,faces=np.concatenate(faces),vertex_colors=np.uint8(rgb*255),process=False)
    mesh.export(args.output/'workspace-surfaces.glb')
    np.savez_compressed(args.output/'surfaces.npz',points=points,colors=rgb,normals=surface_normals,plane_ids=plane_ids,
                        normalization_centre=centre,normalization_radius=radius)
    export_cloud(args.output/'surfaces.ply',points,np.uint8(rgb*255))
    valid_object=np.isfinite(data['depth'])&(data['depth']>0)&data['model_mask']&objects
    object_points=world[valid_object];object_colours=data['rgb'][valid_object]/255
    np.savez_compressed(args.output/'observed-objects.npz',points=object_points,colors=object_colours)
    export_cloud(args.output/'observed-objects.ply',object_points,np.uint8(object_colours*255))
    np.savez_compressed(args.output/'combined.npz',points=np.concatenate([points,object_points]),colors=np.concatenate([rgb,object_colours]))
    np.savez_compressed(args.output/'masks.npz',**masks,excluded=excluded)
    summary={'reference_view':report['image_names'][reference],'planes':plane_reports,'surface_samples':len(points),'object_samples':len(object_points),
             'normalization':{'centre':centre.tolist(),'radius':radius},'metric':False,'scale':'relative; user will supply dimensions later',
             'assumptions':['Tabletop is rectangular and planar; walls are planar. These fitted/infilled shapes are not measurements.',
                            f'Table texture reference: {report["image_names"][reference]}; unseen texture uses nearby observed colour.',
                            'Robot and tripods are separate partial observed geometry; hidden object surfaces are not reconstructed.']}
    (args.output/'report.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
