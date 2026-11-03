import copy
import pytest
from lookandpick.workspaces.service import WorkspaceService
from lookandpick.workspaces.voxels import Grid
from lookandpick.workspaces.voxel_layout import load_policy,geometry_id
from test_voxels import area,anchor

@pytest.fixture
def layout(workspace):
    repo,w=workspace;repo.patch(w['id'],{'working_area':area()});return WorkspaceService(repo.root),w['id']

def edit(s,key,indices,blocked,version=None):
    snap=s.areas.snapshot(key,version)
    return s.areas.edit(key,{'revision':snap['revision'],'policy_revision':snap['policy_revision'],'indices':indices,'blocked':blocked},version)

def test_manual_block_persists_but_unblocking_never_creates_free_sensor_evidence(layout):
    s,key=layout;blocked=edit(s,key,[5],True)
    assert blocked['states'][5]=='occupied' and blocked['observed_states'][5]=='unknown'
    assert WorkspaceService(s.repo.root).areas.snapshot(key)['manual_blocked']==[5]
    cleared=edit(s,key,[5],False)
    assert cleared['states'][5]=='unknown' and cleared['counts']['free']==0
    assert s.repo.load(key)['jobs']==[]

def test_manual_policy_does_not_clear_fresh_live_occupancy(layout):
    s,key=layout;manifest=s.repo.load(key);manifest['draft']['marker_alignment']={**anchor(),'status':'test-marker'};s.repo.write(manifest)
    import time
    snap=s.areas.snapshot(key);g=Grid(area());s.areas.observe(key,{'revision':snap['revision'],'sequence':0,'source':'hand','mode':'points','kind':'hand','frame':'marker_metres','calibration_id':'aruco-test','captured_at_ms':time.time()*1000,'points':[g.centre(5)]})
    edit(s,key,[5],True);cleared=edit(s,key,[5],False)
    assert cleared['states'][5]=='occupied' and cleared['observed_states'][5]=='occupied'

def test_row_edit_active_view_and_version_baseline(layout):
    s,key=layout;v=s.repo.save(key);s.repo.activate(key,v['version_id']);old=s.repo.version(key,v['version_id'])
    edit(s,key,[0,1,2,3],True,v['version_id'])
    assert s.areas.snapshot(key)['manual_blocked']==[0,1,2,3]
    assert s.repo.version(key,v['version_id'])==old
    saved=s.repo.save(key);assert saved['voxel_layout']['blocked']==[0,1,2,3]
    assert s.areas.reset(key)['manual_blocked']==[0,1,2,3]

def test_finer_fidelity_conservatively_preserves_blocked_region(layout):
    s,key=layout;edit(s,key,[5],True);old=s.repo.load(key)['draft'];g=Grid(old['working_area']);point=g.centre(5)
    finer=copy.deepcopy(old['working_area']);finer['cell_size']=.05;s.repo.patch(key,{'working_area':finer})
    snap=s.areas.snapshot(key);assert len(snap['manual_blocked'])==8
    new=Grid(finer);assert new.cells_at(point)<=set(snap['manual_blocked'])
    assert snap['counts']['free']==0

def test_stale_and_invalid_manual_edits_are_rejected(layout):
    s,key=layout;snap=s.areas.snapshot(key);edit(s,key,[1],True)
    for values in ({'policy_revision':snap['policy_revision']},{'revision':'old'},{'indices':[-1]},{'blocked':'true'}):
        payload={'revision':snap['revision'],'policy_revision':s.areas.snapshot(key)['policy_revision'],'indices':[2],'blocked':True,**values}
        with pytest.raises(ValueError):s.areas.edit(key,payload)
    assert s.areas.snapshot(key)['manual_blocked']==[1]
