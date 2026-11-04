"""Deck's state contract and immediate controls with a fake runtime feed."""
import threading
import json
import urllib.request
from http.server import ThreadingHTTPServer
import pytest
from lookandpick.adapters.fake import FakeTracker,SimArm
from lookandpick.deck_runtime import RuntimeSession,ScriptedModel
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime
from lookandpick.mission_control import make_handler

def make_session(provider=None):
    log=CallLog();runtime=Runtime(ScriptedModel(log),arm=SimArm(),tracker=FakeTracker(),log=log,say=lambda _:None)
    return RuntimeSession(runtime,motion_status=provider)

def test_motion_feed_is_copied_into_state_and_replay_without_overriding_safety():
    feed={'stop':{'kind':'fault','reason':'Camera stalled','recovering_in_s':1.2,'resets_left':2},'route':{'kind':'over','waypoints':[[0,0,.3],[.2,0,.3]]},'stopped':False}
    s=make_session(lambda:feed)
    try:
        s.safety.stop();state=s.state()
        assert state['stopped'] and state['stop']==feed['stop'] and state['route']==feed['route']
        assert s.replay()['frames'][-1]['route']==feed['route']
        state['route']['waypoints'][0][0]=99
        assert feed['route']['waypoints'][0][0]==0
    finally:s.close()

def test_runtime_motion_status_is_the_default_provider_and_missing_fields_stay_absent():
    s=make_session()
    try:
        assert 'route' not in s.state() and 'stop' not in s.state()
        s.runtime.motion_status=lambda:{'route':{'kind':'hand','waypoints':[[0,.2,.1]]}}
        assert s.state()['route']['kind']=='hand'
        s.stop();assert s.state()['stop']['kind']=='person'
    finally:s.close()

def test_failed_display_feed_cannot_change_the_stop_latch():
    def failed():raise RuntimeError('status feed unavailable')
    s=make_session(failed)
    try:
        s.stop();state=s.state()
        assert state['stopped'] and state['stop']['kind']=='person' and 'route' not in state
    finally:s.close()

def test_closing_deck_stops_its_recovery_supervisor_once():
    s=make_session();closed=[]
    class Recovery:
        def close(self):closed.append(True)
    s.runtime.recovery=Recovery()
    s.close();s.close();assert closed==[True]

def test_resume_bypasses_busy_slot_without_a_model_and_does_not_reset_the_scene():
    s=make_session();calls=[]
    try:
        s.active='already-running';s.safety.stop()
        def resume():calls.append('resume');s.safety.reset()
        s.runtime.resume=resume
        assert s.submit('carry on')=={'status':'resumed'}
        assert s.active=='already-running' and not s.safety.stopped
        assert calls==['resume'] and not s.runtime.model.requests and not s.arm.log
    finally:s.active=None;s.close()

def test_resume_is_never_sent_to_a_model_when_recovery_is_not_available():
    s=make_session()
    try:
        s.runtime.resume=None  # Explicitly emulate an older runtime, independent of Task 2 landing.
        assert s.state()['motion_controls']['resume'] is False
        with pytest.raises(ValueError,match='recovery support'):s.submit('resume')
        assert not s.runtime.model.requests
    finally:s.close()

def test_cancel_bypasses_busy_slot_without_clearing_a_person_stop():
    s=make_session();calls=[]
    try:
        s.active='already-running';s.safety.stop();s.runtime.cancel=lambda:calls.append('cancel')
        assert s.submit('cancel')=={'status':'cancelled'}
        assert calls==['cancel'] and s.safety.stopped and s.active=='already-running'
        assert not s.runtime.model.requests
    finally:s.active=None;s.close()

def test_http_controls_are_immediate_while_a_command_owns_the_slot():
    s=make_session();calls=[];s.active='running';s.runtime.cancel=lambda:calls.append('cancel')
    def resume():calls.append('resume');s.safety.reset()
    s.runtime.resume=resume;s.safety.stop()
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(s,workspace_service=object()))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        for text,expected in [('cancel','cancelled'),('carry on','resumed')]:
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/command',json.dumps({'text':text}).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=2) as response:assert json.load(response)['status']==expected
        assert calls==['cancel','resume'] and s.active=='running' and not s.runtime.model.requests
    finally:server.shutdown();server.server_close();worker.join(2);s.active=None;s.close()
