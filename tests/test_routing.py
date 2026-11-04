"""Real voxel geometry and ManualZoneGuard; no arm, model or network."""
import math
import pytest
from lookandpick.routing import plan_route
from lookandpick.safety import point_segment_distance
from lookandpick.world import Hand
from lookandpick.workspaces.voxels import Grid
from lookandpick.zones import ManualZoneGuard

AREA={'enabled':True,'centre':[0.,0.],'width':.8,'depth':.8,'height':.6,'base':0.,'yaw':0.,'cell_size':.05,'ttl_ms':500,'hold_ms':750,'margin':0.}
IDENTITY=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
START=(-.25,0.,.08); END=(.25,0.,.08)

def guard_for(predicate):
    grid=Grid(AREA)
    blocked={i for i in range(grid.count) if predicate(grid.centre(i))}
    return ManualZoneGuard(lambda:(grid,blocked,IDENTITY,1.),tool_radius_m=.04)

def reachable(p):return -.35<=p[0]<=.35 and -.35<=p[1]<=.35 and .03<=p[2]<=.35

def checked(route,guard,hands=(),reach=reachable):
    assert route and route[-1]==END
    for a,b in zip([START]+route,route):
        assert reach(b) and guard.violation(a,b) is None
        assert all(point_segment_distance(h.position,a,b)>=.10+h.radius_m-1e-10 for h in hands)

def test_clear_route_is_just_the_destination():
    assert plan_route(START,END,lambda a,b:None,reachable)==[END]

def test_low_wall_routes_up_and_over_with_every_segment_checked():
    guard=guard_for(lambda p:abs(p[0])<.03 and p[2]<.15)
    route=plan_route(START,END,guard.violation,reachable)
    checked(route,guard)
    assert len(route)==3 and route[0][:2]==START[:2] and route[1][:2]==END[:2]
    assert .20<=route[0][2]<=.23

def test_tall_narrow_wall_routes_around():
    guard=guard_for(lambda p:abs(p[0])<.03 and abs(p[1])<.06)
    route=plan_route(START,END,guard.violation,reachable)
    checked(route,guard)
    assert any(abs(p[1])>.10 for p in route) and all(p[2]==START[2] for p in route)

def test_boxed_in_returns_none():
    guard=guard_for(lambda p:abs(p[0])<.03)
    assert plan_route(START,END,guard.violation,reachable) is None

def test_hand_on_one_side_forces_route_to_the_other():
    guard=guard_for(lambda p:abs(p[0])<.03 and abs(p[1])<.06)
    hands=[Hand('left',(0.,-.12,.08),.04)]
    route=plan_route(START,END,guard.violation,reachable,hands=hands)
    checked(route,guard,hands)
    assert all(p[1]>=0 for p in route)

def test_hand_blocks_a_narrow_workspace():
    tight=lambda p:reachable(p) and abs(p[1])<=.04 and p[2]<=.14
    assert plan_route(START,END,lambda a,b:None,tight,hands=[Hand('hand',(0,0,.08),.03)]) is None

def test_hand_envelope_and_exact_clearance_are_respected():
    hand=Hand('hand',(0,.14,.08),.04)
    assert plan_route(START,END,lambda a,b:None,reachable,hands=[hand])==[END]
    hand.position=(0,.139,.08)
    assert plan_route(START,END,lambda a,b:None,reachable,hands=[hand])!=[END]

def test_blocked_endpoint_cannot_be_routed_around():
    guard=guard_for(lambda p:abs(p[0]-.25)<.03 and abs(p[1])<.03 and p[2]<.15)
    assert plan_route(START,END,guard.violation,reachable) is None

def test_each_waypoint_must_pass_reach_and_failures_are_not_hidden():
    assert plan_route(START,END,lambda a,b:None,lambda p:False) is None
    def broken(a,b):raise RuntimeError('zone source unavailable')
    with pytest.raises(RuntimeError,match='unavailable'):plan_route(START,END,broken,reachable)

def test_invalid_geometry_fails_closed_and_invalid_settings_are_rejected():
    assert plan_route((math.nan,0,0),END,lambda a,b:None,reachable) is None
    assert plan_route(START,END,lambda a,b:None,reachable,hands=[Hand('bad',(0,0,0),-1)]) is None
    for setting in ({'margin_m':-1},{'ceiling_m':math.inf},{'hand_clearance_m':True}):
        with pytest.raises(ValueError):plan_route(START,END,lambda a,b:None,reachable,**setting)
