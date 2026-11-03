import copy
import time
import pytest
from lookandpick.workspaces.service import WorkspaceService
from lookandpick.workspaces.schema import IDENTITY
from test_voxels import area,anchor
from test_workspace_http import request


@pytest.fixture
def service(workspace):
    repo,w=workspace;s=WorkspaceService(repo.root);repo.patch(w['id'],{'working_area':area()})
    value=repo.load(w['id']);value['draft']['marker_alignment']={**anchor(),'marker_from_canonical':copy.deepcopy(IDENTITY),'status':'test-marker'};repo.write(value)
    return s,w['id']


def test_area_persists_without_inference_and_active_version_is_immutable(service):
    s,key=service;v=s.repo.save(key);s.repo.activate(key,v['version_id']);old=s.repo.active()
    assert request(s,f'/api/workspaces/{key}/draft','PATCH',{'working_area':area(height=.3)}).status==200
    assert s.repo.active()==old and s.repo.load(key)['jobs']==[]
    snap=s.areas.snapshot(key);assert len(snap['states'])==48
    fresh=WorkspaceService(s.repo.root)
    assert fresh.areas.snapshot(key)['counts']['unknown']==48
    assert s.repo.version(key,v['version_id'])['working_area']['height']==.2
    assert request(s,f'/api/workspaces/{key}/draft','PATCH',{'marker_alignment':anchor()}).status==400


def test_only_matching_active_version_receives_transient_live_observations(service):
    s,key=service;version=s.repo.save(key);s.repo.activate(key,version['version_id']);snapshot=s.areas.snapshot(key)
    payload={'revision':snapshot['revision'],'sequence':0,'source':'test-hand','mode':'points','kind':'hand','frame':'marker_metres','calibration_id':'aruco-test','points':[[.01,.01,.05]],'captured_at_ms':time.time()*1000}
    s.areas.observe(key,payload)
    assert s.areas.snapshot(key,version['version_id'])['counts']['occupied']>0
    s.repo.patch(key,{'working_area':area(height=.3)})
    assert s.areas.snapshot(key)['counts']['occupied']==0
    with pytest.raises(ValueError,match='changed'):s.areas.observe(key,{**payload,'sequence':1})
    s.areas.reset(key)
    assert s.areas.snapshot(key,version['version_id'])['counts']['occupied']==0


def test_rgb_class_filter_projection_and_stale_frame_fail_closed(service,monkeypatch):
    s,key=service;s.areas.cameras['camera-test']={'key':key,'version':None,'alignment':'aruco-test','pose':{'camera_matrix':[[100,0,100],[0,100,100],[0,0,1]],'rotation':[[1,0,0],[0,-1,0],[0,0,-1]],'translation':[0,0,1],'dist':[],'width':200,'height':200}}
    snapshot=s.areas.snapshot(key);base={'camera_id':'camera-test','revision':snapshot['revision'],'sequence':0,'source':'rgb-test','captured_at_ms':time.time()*1000}
    objects=s.areas.rgb(key,{**base,'detections':[{'kind':'cup','box':[0,0,1,1]}]})
    assert objects['counts']['occupied']==0 and objects['counts']['free']==0
    human=s.areas.rgb(key,{**base,'sequence':1,'detections':[{'kind':'hand','box':[.35,.35,.65,.65]}]})
    assert human['counts']['occupied']>0 and human['counts']['free']==0
    # Cached projection and fresh projection produce the same state.
    assert s.areas.rgb(key,{**base,'sequence':2,'detections':[{'kind':'person','box':[.35,.35,.65,.65]}]})['states']==human['states']
    monkeypatch.setattr(s.areas.detector,'detect',lambda data,**kwargs:{'width':200,'height':200,'detections':[]})
    times=iter([0,1]);monkeypatch.setattr('lookandpick.workspaces.areas.time.monotonic',lambda:next(times))
    with pytest.raises(ValueError,match='stale'):s.areas.detect(key,{**base,'sequence':3,'jpeg':'test'})


def test_camera_and_observation_routes_reject_missing_bindings_and_old_revisions(service):
    s,key=service
    assert request(s,f'/api/workspaces/{key}/voxels').status==200
    assert request(s,f'/api/workspaces/{key}/voxel-observations','POST',{'revision':'obsolete'}).status==400
    assert request(s,f'/api/workspaces/{key}/voxel-rgb','POST',{'camera_id':'missing','detections':[]}).status==400
    assert request(s,f'/api/workspaces/{key}/marker-alignment','POST',{'view_id':'../../.env','marker_size_m':.12}).status==400


def test_reset_revokes_inflight_observations_without_changing_saved_geometry(service):
    s,key=service;snap=s.areas.snapshot(key);next_snap=s.areas.reset(key)
    assert next_snap['revision']!=snap['revision']
    payload={'revision':snap['revision'],'sequence':99,'source':'inflight','mode':'simulation','free':[0]}
    with pytest.raises(ValueError,match='changed'):s.areas.observe(key,payload)
    assert s.areas.snapshot(key)['counts']['free']==0


def test_path_api_requires_explicit_frame_and_marker_binding(service):
    s,key=service
    with pytest.raises(ValueError,match='frame'):s.areas.path(key,{'start':[0,0,.05],'end':[.05,0,.05]})
    with pytest.raises(ValueError,match='calibration'):s.areas.path(key,{'frame':'marker_metres','start':[0,0,.05],'end':[.05,0,.05]})
    result=s.areas.path(key,{'frame':'marker_metres','calibration_id':'aruco-test','start':[0,0,.05],'end':[.05,0,.05]})
    assert not result['allowed'] and result['physical_motion_authorised'] is False
