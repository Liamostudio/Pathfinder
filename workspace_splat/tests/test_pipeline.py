from pathlib import Path
import json
import cv2
import numpy as np
from PIL import Image
import pytest
from workspace_splat.ingest import ingest
from workspace_splat.formats import export,SH_C0
from workspace_splat.server import Library


def test_image_ingest_orientation_duplicates_and_limits(tmp_path):
    source=tmp_path/'source';source.mkdir()
    rng=np.random.default_rng(2)
    image=Image.fromarray(rng.integers(0,255,(600,800,3),dtype=np.uint8))
    image.save(source/'a.png');image.save(source/'duplicate.png')
    Image.fromarray(rng.integers(0,255,(600,800,3),dtype=np.uint8)).save(source/'b.png')
    output=tmp_path/'capture'
    result=ingest([source],output,max_edge=400)
    assert len(result['images'])==2
    assert result['skipped'][0]['reason']=='exact duplicate'
    assert Image.open(output/'images/00000.jpg').size==(400,300)
    assert result['warnings']
    with pytest.raises(ValueError,match='new or empty'):ingest([source],output)


def test_video_sampling_and_recorder_sidecar(tmp_path):
    path=tmp_path/'clip.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(320,240))
    assert writer.isOpened()
    for i in range(20):
        frame=np.full((240,320,3),i*10,np.uint8);cv2.putText(frame,str(i),(30,100),cv2.FONT_HERSHEY_SIMPLEX,1,(0,255,0),2);writer.write(frame)
    writer.release()
    path.with_suffix('.json').write_text(json.dumps([100+i/10 for i in range(20)]))
    result=ingest([path],tmp_path/'capture',fps=2,max_frames=10)
    assert len(result['images'])==4
    assert [r['timestamp_s'] for r in result['images']]==[100,100.5,101,101.5]


def test_export_gaussian_binary_and_standard_ply(tmp_path):
    means=np.array([[1,2,3],[2,3,4]],dtype=np.float32)
    scales=np.array([[.01,.02,.03],[.1,.2,.3]],dtype=np.float32)
    rotations=np.array([[1,0,0,0],[0,1,0,0]],dtype=np.float32)
    colors=np.array([[1,.5,0],[0,.2,1]],dtype=np.float32)
    opacity=np.array([.5,.8],dtype=np.float32)
    result=export(tmp_path,means,scales,rotations,colors,opacity)
    raw=(tmp_path/'scene.splat').read_bytes()
    assert len(raw)==64 and result['count']==2
    assert np.allclose(np.frombuffer(raw[:24],dtype='<f4'),np.r_[means[0],scales[0]])
    assert list(raw[24:28])==[255,128,0,128]
    assert raw[28:32]==bytes([255,128,128,128])
    header,body=(tmp_path/'scene.ply').read_bytes().split(b'end_header\n')
    assert b'property float opacity' in header
    rows=np.frombuffer(body,dtype='<f4').reshape(2,17)
    assert np.allclose(rows[:,6:9]*SH_C0+.5,colors)
    assert np.allclose(np.exp(rows[:,10:13]),scales)
    assert np.allclose(1/(1+np.exp(-rows[:,9])),opacity)
    assert np.allclose(rows[:,13:17],rotations)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1])
def test_export_rejects_invalid_scales(tmp_path,bad):
    with pytest.raises(ValueError):export(tmp_path,np.zeros((1,3)),np.full((1,3),bad),np.array([[1,0,0,0]]),np.ones((1,3)),np.array([.5]))


def test_library_private_paths_and_upload_limits(tmp_path):
    library=Library(tmp_path/'data');key=library.new()
    with pytest.raises(ValueError):library.capture('../../etc')
    with pytest.raises(ValueError):library.upload(key,'../scene.jpg',b'not-image')
    with pytest.raises(ValueError):library.upload(key,'code.py',b'print(1)')
    library.upload(key,'image.jpg',b'jpeg-data')
    with pytest.raises(ValueError):library.upload(key,'image.jpg',b'duplicate')
    assert library.status(key)['trained'] is False
    with pytest.raises(ValueError):library.launch(key,steps=0)


def test_ingest_blank_input_fails_instead_of_fake_reconstruction(tmp_path):
    from workspace_splat.reconstruct import reconstruct
    source=tmp_path/'source';source.mkdir()
    Image.new('RGB',(320,240),(100,100,100)).save(source/'a.jpg')
    Image.new('RGB',(320,240),(200,200,200)).save(source/'b.jpg')
    capture=tmp_path/'capture';ingest([source],capture)
    with pytest.raises(ValueError,match='could not reconstruct'):reconstruct(capture)
    assert not (capture/'output/scene.splat').exists()


def test_frame_source_adapter_preserves_bgr_and_timestamps(tmp_path):
    from workspace_splat.ingest import ingest_frames
    class Source:
        name='recorded-workspace'
        def frames(self):
            for i in range(3):
                frame=np.zeros((240,320,3),np.uint8);frame[:]=(50+i*30,10,220)
                yield 10+i*.1,frame
    manifest=ingest_frames(Source(),tmp_path/'capture')
    assert [r['timestamp_s'] for r in manifest['images']]==[10,10.1,10.2]
    rgb=np.array(Image.open(tmp_path/'capture/images/00000.jpg'))
    assert rgb[0,0,0]>200 and rgb[0,0,2]<60
