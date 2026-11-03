import copy
import math
import time
import pytest
from lookandpick.workspaces.voxels import Grid,Occupancy,validate_area,signature
from lookandpick.workspaces.schema import IDENTITY


def area(**changes):
    a={'enabled':True,'centre':[0.,0.],'width':.4,'depth':.4,'height':.2,'base':0.,'yaw':0.,'cell_size':.1,'ttl_ms':500,'hold_ms':750,'margin':0.}
    return {**a,**changes}


def anchor():return {'id':'aruco-test','canonical_from_marker':copy.deepcopy(IDENTITY),'canonical_units_per_metre':1.,'metric_inputs_complete':True}

def observation(mapping,**changes):return {'revision':mapping.revision,'source':'camera-a','sequence':0,'mode':'points','kind':'hand','frame':'marker_metres','calibration_id':'aruco-test','points':[],'free':[],'captured_at_ms':time.time()*1000,**changes}


def test_rotated_grid_round_trip_boundaries_and_whole_cubes():
    g=Grid(area(centre=[1,2],yaw=45,width=.31))
    assert g.shape==(4,4,2) and g.count==32 and g.area['width']==.31
    for index in range(g.count):assert index in g.cells_at(g.centre(index))
    # Both touching cubes are unsafe at an exact boundary.
    assert len(g.cells_at(g.world([.1,.1,.1])))==8
    assert not g.cells_at(g.world([-.2,0,0]))


@pytest.mark.parametrize('change',[{'cell_size':0},{'width':float('nan')},{'height':-1},{'width':10,'depth':10,'height':10,'cell_size':.005},{'enabled':1}])
def test_unbounded_or_invalid_grids_rejected(change):
    with pytest.raises(ValueError):validate_area(area(**change))


def test_unknown_is_unsafe_free_expires_and_occupied_hold_is_conservative():
    now=[0.];g=Grid(area());m=Occupancy(g,'revision',lambda:now[0])
    p=g.centre(3);assert not m.check_path(p,p)['allowed']
    m.observe(observation(m,points=[p],free=list(range(g.count))),anchor())
    assert m.snapshot()['states'][3]=='occupied'
    now[0]=.6;assert m.snapshot()['states'][3]=='occupied'
    assert m.snapshot()['counts']['free']==0
    now[0]=.8;assert m.snapshot()['counts']['unknown']==g.count
    assert not m.check_path(p,p)['allowed']


def test_occupied_wins_over_other_sources_clear_evidence():
    now=[0.];g=Grid(area(hold_ms=0,ttl_ms=2000));m=Occupancy(g,'revision',lambda:now[0]);p=g.centre(5)
    m.observe(observation(m,points=[p]),anchor());now[0]=1
    m.observe(observation(m,source='camera-b',free=[5]),anchor())
    assert m.snapshot()['states'][5]=='occupied'
    now[0]=2.1;assert m.snapshot()['states'][5]=='free'


def test_stale_sequence_wrong_frame_old_revision_and_objects_do_not_mutate():
    g=Grid(area());m=Occupancy(g,'revision');m.observe(observation(m),anchor());before=m.snapshot()['states']
    for changes in ({'sequence':0},{'revision':'old','sequence':1},{'frame':'world','sequence':1},{'kind':'cup','sequence':1},{'free':[-1],'sequence':1},{'points':[[0,0,float('nan')]],'sequence':1}):
        with pytest.raises(ValueError):m.observe(observation(m,**changes),anchor())
        assert m.snapshot()['states']==before


def test_rgb_cannot_mark_cells_free_or_mix_live_and_simulation():
    m=Occupancy(Grid(area()),'revision')
    with pytest.raises(ValueError,match='RGB'):m.observe(observation(m,mode='rgb_regions',free=[0]),anchor())
    m.observe(observation(m,mode='simulation',free=[0]),None)
    with pytest.raises(ValueError,match='Reset'):m.observe(observation(m,mode='rgb_regions',source='live'),anchor())
    assert m.check_path(m.grid.centre(0),m.grid.centre(0))['physical_motion_authorised'] is False


def test_swept_radius_and_outside_area_are_rejected():
    g=Grid(area());m=Occupancy(g,'revision')
    m.observe(observation(m,free=list(range(g.count))),anchor())
    start,end=g.centre(0),g.centre(3)
    assert m.check_path(start,end)['allowed']
    assert not m.check_path(start,end,.06)['allowed']
    assert m.check_path(start,[10,0,0])['reason']=='outside_working_area'
    m.observe(observation(m,sequence=1,points=[g.centre(2)]),anchor())
    assert not m.check_path(start,end)['allowed']


def test_grid_and_calibration_revision_invalidate_old_observations():
    a=area();assert signature(a)!=signature(area(height=.3));assert signature(a,anchor())!=signature(a)


def test_non_decimal_marker_scale_does_not_grow_area_on_repeated_edits():
    a=area(cell_size=.05820516940772633,width=.3,depth=.2,height=.35)
    initial=validate_area(a)
    for _ in range(20):assert validate_area(initial)==initial


def test_red_hold_does_not_expire_other_fresh_green_cells_in_the_display():
    now=[0.];m=Occupancy(Grid(area()),'revision',lambda:now[0]);g=m.grid
    m.observe(observation(m,points=[g.centre(0)],free=list(range(g.count))),anchor())
    now[0]=.49
    m.observe(observation(m,sequence=1,points=[g.centre(3)],free=list(range(g.count))),anchor())
    snapshot=m.snapshot()
    assert snapshot['states'][0]=='occupied' and snapshot['states'][1]=='free'
    assert snapshot['valid_for_ms']>=499


def test_fidelity_change_preserves_volume_bounds_and_clips_edge_cells():
    coarse=Grid(area(width=.31,depth=.27,height=.23,cell_size=.1));fine=Grid(area(width=.31,depth=.27,height=.23,cell_size=.05))
    assert coarse.area['width']==fine.area['width']==.31
    assert coarse.area['depth']==fine.area['depth']==.27
    assert not coarse.cells_at(coarse.world([.35,.1,.1]))
    last=coarse.corners(coarse.count-1)
    assert max(coarse.local(p)[0] for p in last)==pytest.approx(.31)
    assert coarse.path_cells(coarse.world([.305,.1,.1]),coarse.world([.34,.1,.1])) is None
