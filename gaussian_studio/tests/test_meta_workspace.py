import numpy as np
from scipy.spatial.transform import Rotation
from gaussian_studio.workspace_surfaces import fit_plane,plane_grid
from gaussian_studio.surface_splat import normal_quaternions,export_full_sh
from gaussian_studio.stage_video import camera_path


def test_plane_fit_recovers_tilted_surface_with_outliers():
    rng=np.random.default_rng(7);xy=rng.uniform(-1,1,(1000,2))
    normal=np.array([.1,-.9,-.4]);normal/=np.linalg.norm(normal)
    z=-(xy@normal[:2]+.7)/normal[2]
    points=np.column_stack([xy,z])+rng.normal(0,.001,(1000,3))
    points=np.concatenate([points,rng.uniform(-2,2,(200,3))])
    n,d,inliers=fit_plane(points,.01,up=normal)
    assert n@normal>.999 and abs(d-.7)<.003
    assert inliers[:1000].mean()>.99 and inliers[1000:].mean()<.03


def test_vertical_plane_prior_rejects_dominant_floor():
    rng=np.random.default_rng(8);xy=rng.uniform(-1,1,(1400,2))
    floor=np.column_stack([xy[:1000],np.zeros(1000)])
    wall=np.column_stack([np.ones(400),xy[1000:]])
    n,d,_=fit_plane(np.concatenate([floor,wall]),.005,up=np.array([0,0,1]),vertical=True)
    assert abs(n[0])>.999 and abs(abs(d)-1)<.003


def test_plane_mesh_stays_on_plane_and_has_valid_triangles():
    rng=np.random.default_rng(2);points=np.column_stack([rng.uniform(-1,1,(1000,2)),np.full(1000,2.)])
    vertices,faces,footprint=plane_grid(points,np.array([0,0,1.]),-2.,.08,rectangle=True)
    assert np.allclose(vertices[:,2],2.)
    assert faces.min()>=0 and faces.max()<len(vertices) and len(faces)>100
    area=np.linalg.norm(np.cross(vertices[faces[:,1]]-vertices[faces[:,0]],vertices[faces[:,2]]-vertices[faces[:,0]]),axis=1)
    assert (area>0).all() and footprint['rectangle_assumption']


def test_surface_gaussian_rotation_aligns_thin_axis_with_normal():
    normals=np.array([[0,0,1.],[0,1,0],[1,0,0],[.2,-.8,.4]])
    normals/=np.linalg.norm(normals,axis=1,keepdims=True)
    q=normal_quaternions(normals)
    rotations=Rotation.from_quat(q[:,[1,2,3,0]]).as_matrix()
    assert np.allclose(rotations[:,:,2],normals,atol=1e-6)


def test_video_path_uses_camera_to_world_translation_and_shared_intrinsics():
    poses=np.tile(np.eye(4,dtype=np.float32),(4,1,1));poses[:,0,3]=[-1,-2,-3,-4]
    K=np.tile(np.array([[100.,0,50],[0,100,40],[0,0,1]],dtype=np.float32),(4,1,1))
    data={'extrinsics':poses,'intrinsics':K,'depth':np.ones((4,80,100))}
    path=camera_path(data,16,200,160)
    assert np.allclose(path[0][0],poses[2]) and np.allclose(path[4][0],poses[3])
    assert np.allclose(path[0][1],np.diag([2,2,1])@K[2])
    for pose,_ in path:
        assert np.allclose(pose[:3,:3]@pose[:3,:3].T,np.eye(3),atol=1e-6)


def test_standard_ply_preserves_channel_major_sh_coefficients(tmp_path):
    coefficients=np.arange(12,dtype=np.float32).reshape(1,4,3)/10
    values={'means':np.ones((1,3)), 'scales':np.ones((1,3)),
            'quats':np.array([[1.,0,0,0]]),'opacities':np.array([.5])}
    path=tmp_path/'scene.ply';export_full_sh(path,values,coefficients)
    header,body=path.read_bytes().split(b'end_header\n')
    fields=[line.split()[-1].decode() for line in header.splitlines() if line.startswith(b'property float')]
    values=dict(zip(fields,np.frombuffer(body,dtype='<f4')))
    assert values['f_dc_1']==coefficients[0,0,1]
    assert values['f_rest_0']==coefficients[0,1,0]
    assert values['f_rest_3']==coefficients[0,1,1]
    assert values['f_rest_8']==coefficients[0,3,2]
