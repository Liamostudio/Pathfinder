"""Read-only, CPU-only marker-plane appearance cache. Never edits a workspace."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

def registration(manifest):
    """Table corners and UVs in the same marker plane used by rectify."""
    import numpy as np
    table=next(s for s in manifest['surfaces'] if s['name']=='table' and s['enabled'])
    anchor=manifest.get('marker_alignment')
    if not anchor:raise ValueError('No marker anchor; using projected appearance')
    transform=np.asarray(anchor['canonical_from_marker'],float)
    inverse=np.linalg.inv(transform)
    yaw=math.radians(table['yaw']);c,s=math.cos(yaw),math.sin(yaw)
    corners=[]
    for x,y in [(-1,-1),(1,-1),(1,1),(-1,1)]:
        x*=table['width']/2;y*=table['depth']/2
        corners.append([table['centre'][0]+c*x-s*y,table['centre'][1]+s*x+c*y,table['height']])
    marker=(inverse@np.column_stack([corners,np.ones(4)]).T).T[:,:2]
    x0,y0=marker.min(axis=0);x1,y1=marker.max(axis=0)
    return {'extent':[x0,x1,y0,y1], 'corners':corners,
            'uv':[[float((x-x0)/(x1-x0)),float((y1-y)/(y1-y0))] for x,y in marker],
            'frame':'marker_metres','marker_m':anchor['marker_size_m'],'mm_per_px':0.75}


def build(request):
    import cv2
    import numpy as np
    from ..calibration import detect_marker
    from ..table_texture import rectify
    manifest=request['manifest'];result=registration(manifest);best=None
    for path in request['sources']:
        image=cv2.imread(path)
        if image is None:continue
        corners=detect_marker(image,manifest['marker_alignment'].get('marker_id',0),manifest['marker_alignment'].get('dictionary','DICT_4X4_50'))
        if corners is None:continue
        p=np.asarray(corners).reshape(4,2);edges=np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1)
        # Large marker, with balanced edges: favour fronto-parallel source views.
        score=abs(cv2.contourArea(p.astype(np.float32)))*(edges.min()/edges.max())**2
        if best is None or score>best[0]:best=(score,image,p,Path(path).name)
    if best is None:raise ValueError('No source photo shows the marker; using projected appearance')
    _,image,corners,source=best
    rgba=cv2.cvtColor(image,cv2.COLOR_BGR2BGRA)
    mask=request.get('masks',{}).get(source)
    if mask:
        with np.load(mask,allow_pickle=False) as masks:
            table=masks['table'].astype(np.uint8);valid=np.zeros(table.shape,np.uint8)
            pixels=cv2.findNonZero(table)
            if pixels is not None:cv2.fillConvexPoly(valid,cv2.convexHull(pixels),1)
            valid=valid.astype(bool)
            for kind in ('robot','tripod','chair'):
                if kind in masks:valid &= ~cv2.dilate(masks[kind].astype(np.uint8),np.ones((7,7),np.uint8)).astype(bool)
            if valid.shape!=image.shape[:2]:raise ValueError('Table mask does not match source')
            rgba[:,:,3]=valid.astype(np.uint8)*255
    out=rectify(rgba,corners,result['marker_m'],tuple(result['extent']),result['mm_per_px'])
    target=Path(request['output']);target.parent.mkdir(parents=True,exist_ok=True)
    if not cv2.imwrite(str(target),out):raise ValueError('Cannot cache table appearance')
    result.update(source=source,size_px=[out.shape[1],out.shape[0]],status='marker_plane')
    target.with_suffix('.json').write_text(json.dumps(result,allow_nan=False))
    return result


def cached(service,key,version=None):
    """Generate a derived sidecar, without rewriting draft or immutable version."""
    manifest=service.repo.version(key,version) if version else service.repo.load(key)['draft']
    if not manifest or not manifest.get('marker_alignment'):raise ValueError('No marker anchor; using projected appearance')
    sources=[service.repo.resolve(key,path) for name,path in manifest['assets'].items() if name.startswith('source-')]
    masks={}
    try:
        completed=json.loads(service.repo.resolve(key,'runs/'+manifest['run_id']+'/completed.json').read_text())
        folder=completed['segmentation']['path']
        for source in sources:masks[source.name]=str(service.repo.resolve(key,folder+'/'+source.stem+'.npz'))
    except (FileNotFoundError,KeyError):pass
    signature=json.dumps([5,manifest['surfaces'],manifest['marker_alignment'],[(str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in sources],masks],sort_keys=True)
    digest=hashlib.sha256(signature.encode()).hexdigest()[:24]
    target=service.repo.folder(key)/'visual-cache'/f'table-{digest}.png'
    with service.lock:
        if not target.exists() or not target.with_suffix('.json').exists():
            request={'manifest':manifest,'sources':[str(p) for p in sources],'masks':masks,'output':str(target)}
            try:
                process=subprocess.run([service.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.table_appearance'],input=json.dumps(request),text=True,capture_output=True,timeout=25,cwd=Path(__file__).resolve().parents[2])
            except (OSError,subprocess.TimeoutExpired):raise ValueError('Table texture unavailable; using projected appearance') from None
            if process.returncode:raise ValueError('Marker-plane texture unavailable; using projected appearance')
    return target,json.loads(target.with_suffix('.json').read_text())


if __name__=='__main__':
    build(json.load(sys.stdin))
