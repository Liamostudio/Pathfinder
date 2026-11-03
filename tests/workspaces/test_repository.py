import copy
from io import BytesIO
import json
from pathlib import Path
import pytest
from lookandpick.workspaces.repository import Repository,atomic_json
from lookandpick.workspaces.schema import IDENTITY,canonical_frame,settings,cache_keys,transform



def test_version_is_immutable_and_failed_rebuild_does_not_replace_active(workspace):
    repo,w=workspace;key=w['id'];first=repo.save(key);repo.activate(key,first['version_id'])
    surfaces=copy.deepcopy(w['draft']['surfaces']);surfaces[0]['width']=2
    edited=repo.patch(key,{'surfaces':surfaces})
    assert edited['draft']['needs_rebuild']
    with pytest.raises(ValueError,match='Rebuild'):repo.save(key)
    assert repo.active()['surfaces'][0]['width']==1
    assert Repository(repo.root).active()==first
    assert repo.version(key,'v0001')['surfaces'][0]['width']==1


def test_robot_and_scale_are_composition_only_and_do_not_enable_alignment(workspace):
    repo,w=workspace
    value=repo.patch(w['id'],{'robot':{'translation':[.2,.3,0.]},'physical_width_m':1.2})
    draft=value['draft'];assert not draft['needs_rebuild']
    assert draft['calibration']['meters_per_map_unit']==1.2
    assert draft['calibration']['robot_alignment_status']=='approximate'
    assert draft['frames']['workspace_from_map'][0][0]==1.2
    assert repo.patch(w['id'],{'physical_width_m':None})['draft']['units']=='relative'


@pytest.mark.parametrize('patch',[{'assets':{'scene.splat':'/etc/passwd'}},{'calibration':{'robot_alignment_status':'calibrated'}},{'robot':{'translation':[float('nan'),0,0]}},{'layers':{'background':'yes'}}])
def test_untrusted_draft_changes_rejected_without_mutating_workspace(workspace,patch):
    repo,w=workspace;before=repo.load(w['id'])
    with pytest.raises((ValueError,KeyError)):repo.patch(w['id'],patch)
    assert repo.load(w['id'])==before


def test_sources_stream_in_chunks_and_interrupted_upload_does_not_publish(tmp_path):
    repo=Repository(tmp_path);key=repo.create('Capture')['id']
    class Chunks(BytesIO):
        def read(self,size):assert size<=1024**2;return super().read(size)
    value=repo.upload(key,'clip.mp4',Chunks(b'x'*1400000),1400000)
    assert value['sources'][0]['bytes']==1400000
    with pytest.raises(ValueError,match='interrupted'):repo.upload(key,'other.mp4',Chunks(b'too short'),100)
    assert not (repo.folder(key)/'sources/other.mp4').exists()
    assert len(repo.load(key)['sources'])==1
    repo.remove_source(key,value['sources'][0]['id'])
    assert (repo.folder(key)/'sources/clip.mp4').is_file()


def test_paths_and_symlink_escape_are_rejected(workspace,tmp_path):
    repo,w=workspace
    for key in ('../outside','/tmp','a/b'):
        with pytest.raises(ValueError):repo.folder(key)
    with pytest.raises(FileNotFoundError):repo.resolve(w['id'],'../../outside')
    outside=tmp_path/'private';outside.write_text('private')
    (repo.folder(w['id'])/'escape').symlink_to(outside)
    with pytest.raises(FileNotFoundError):repo.resolve(w['id'],'escape')


def test_cache_invalidation_respects_stage_dependencies():
    cfg=settings();sources=[{'name':'a.jpg','sha256':'abc'}];a=cache_keys(cfg,sources)
    masks=copy.deepcopy(cfg);masks['prompts']['table']=['wooden table'];b=cache_keys(masks,sources)
    assert a['geometry']==b['geometry'] and a['segmentation']!=b['segmentation'] and a['appearance']!=b['appearance']
    table=copy.deepcopy(cfg);table['surface_overrides']=[{'width':2}];c=cache_keys(table,sources)
    assert a['geometry']==c['geometry'] and a['segmentation']==c['segmentation'] and a['appearance']!=c['appearance']
    video=copy.deepcopy(cfg);video['preview_orbit']=True;d=cache_keys(video,sources)
    assert all(a[k]==d[k] for k in ['geometry','segmentation','surfaces','appearance']) and a['previews']!=d['previews']
    views=copy.deepcopy(cfg);views['view_ids']=['00000.jpg','00001.jpg'];e=cache_keys(views,sources)
    assert a['ingest']==e['ingest'] and a['geometry']!=e['geometry'] and a['segmentation']!=e['segmentation']


def test_canonical_table_frame_is_z_up_and_centred():
    table={'normal':[0,1,0],'origin':[1,2,3],'axis_u':[1,0,0],'axis_v':[0,0,-1],'footprint':[[-1,-.5],[1,-.5],[1,.5],[-1,.5]]}
    frame,surface=canonical_frame(table)
    assert transform(frame,[1,2,3])==pytest.approx([0,0,0])
    assert frame[2][:3]==pytest.approx([0,1,0]);assert surface['width']==2;assert surface['depth']==1


def test_joint_budget_and_nonfinite_settings_are_rejected():
    for cfg in [{'max_views':5},{'video_frames':0},{'video_fps':float('nan')},{'view_ids':['00000.jpg']},{'output':'/tmp'}]:
        with pytest.raises(ValueError):settings(cfg)


def test_visual_joint_limits_and_base_transform_are_validated(workspace):
    repo,w=workspace
    edited=repo.patch(w['id'],{'robot':{'translation':[.2,0,0],'joint_positions':{'shoulder_pan':.1}},'physical_width_m':2})
    assert edited['draft']['robot']['workspace_from_robot_base'][0][3]==pytest.approx(.4)
    for joints in ({'shoulder_pan':100},{'made_up':0},[]):
        with pytest.raises(ValueError):repo.patch(w['id'],{'robot':{'joint_positions':joints}})
