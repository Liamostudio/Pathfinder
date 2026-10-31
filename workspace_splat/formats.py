"""Portable 3DGS PLY and 32-byte .splat export; rotations are w,x,y,z."""
from pathlib import Path
import numpy as np

SH_C0=0.28209479177387814


def validate(means,scales,quats,colors,opacities):
    arrays=[np.asarray(x,dtype=np.float32) for x in (means,scales,quats,colors,opacities)]
    n=len(arrays[0])
    if any(x.shape!=shape for x,shape in zip(arrays,[(n,3),(n,3),(n,4),(n,3),(n,)])) or n==0:
        raise ValueError('Invalid Gaussian array shapes')
    if not all(np.isfinite(x).all() for x in arrays) or np.any(arrays[1]<=0):raise ValueError('Gaussians must be finite with positive scales')
    lengths=np.linalg.norm(arrays[2],axis=1,keepdims=True)
    if np.any(lengths<1e-8):raise ValueError('Invalid quaternion')
    arrays[2]=arrays[2]/lengths
    arrays[3]=np.clip(arrays[3],0,1);arrays[4]=np.clip(arrays[4],1e-6,1-1e-6)
    return arrays


def export(directory,means,scales,quats,colors,opacities):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    means,scales,quats,colors,opacities=validate(means,scales,quats,colors,opacities)
    n=len(means)
    packed=np.zeros(n,dtype=[('position','<f4',3),('scale','<f4',3),('rgba','u1',4),('rotation','u1',4)])
    packed['position']=means;packed['scale']=scales
    packed['rgba'][:,:3]=np.rint(colors*255).astype('u1');packed['rgba'][:,3]=np.rint(opacities*255).astype('u1')
    packed['rotation']=np.clip(np.rint(quats*128+128),0,255).astype('u1')
    (directory/'scene.splat').write_bytes(packed.tobytes())
    props=['x','y','z','nx','ny','nz','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    rows=np.column_stack((means,np.zeros_like(means),(colors-.5)/SH_C0,np.log(opacities/(1-opacities)),np.log(scales),quats)).astype('<f4')
    header='ply\nformat binary_little_endian 1.0\ncomment Pathfinder Gaussian splat; SH degree 0\nelement vertex '+str(n)+'\n'+''.join('property float '+p+'\n' for p in props)+'end_header\n'
    (directory/'scene.ply').write_bytes(header.encode()+rows.tobytes())
    np.savez_compressed(directory/'gaussians.npz',means=means,scales=scales,quats=quats,colors=colors,opacities=opacities)
    return {'count':n,'splat_bytes':packed.nbytes,'ply_bytes':len(header.encode())+rows.nbytes}
