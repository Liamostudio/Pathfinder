import threading
import time
from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.deck_runtime import RuntimeSession, ScriptedModel
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime

class SlowArm(SimArm):
    def __init__(self): super().__init__(); self.entered=threading.Event(); self.release=threading.Event()
    def move_to(self,p,s):
        self.entered.set(); self.release.wait(2)
        return super().move_to(p,s)
    def halt(self): super().halt(); self.release.set()

def test_stop_interrupts_worker_and_prevents_more_model_calls():
    arm=SlowArm(); log=CallLog(); model=ScriptedModel(log)
    session=RuntimeSession(Runtime(model,arm=arm,tracker=FakeTracker(),log=log,say=lambda _:None))
    try:
        key=session.submit('pick red cube and put next to blue cube')['job_id']
        assert arm.entered.wait(1)
        before=len(model.requests); t=time.monotonic(); session.stop()
        assert time.monotonic()-t<.1
        time.sleep(.4)
        assert session.job(key)['status']=='running'          # a STOP mid-action HOLDS the instruction: 'carry on' resumes it, 'cancel' ends it
        assert session.state()['stopped']
        session.cancel()
        for _ in range(200):
            if session.job(key)['status']!='running': break
            time.sleep(.01)
        assert session.job(key)['status']=='stopped'
        assert len(model.requests)==before
        assert session.state()['stopped']
    finally: session.close()

def test_simulated_gate_and_timeline_schema():
    log=CallLog(); arm=SimArm(); session=RuntimeSession(Runtime(ScriptedModel(log),arm=arm,tracker=FakeTracker(),log=log))
    try:
        state=session.state(); assert state['hand_gate']['simulated']
        assert all(k in session.timeline()[0] for k in ('id','time','source','input','output','latency_ms','cost_usd','tokens'))
        assert session.replay()['frames']
    finally: session.close()

def test_stop_during_model_request_discards_late_motion():
    from lookandpick.models.base import ChatResult,ToolCall
    class PendingModel:
        name='pending model'
        def __init__(self):self.entered=threading.Event();self.release=threading.Event();self.calls=0
        def chat(self,*args):
            self.calls+=1;self.entered.set();self.release.wait(2)
            return ChatResult(tool_calls=(ToolCall('1','pick','{"id":"obj1"}'),))
    model=PendingModel();arm=SimArm();session=RuntimeSession(Runtime(model,arm=arm,tracker=FakeTracker(),log=CallLog(),say=lambda _:None))
    try:
        key=session.submit('pick the red cube')['job_id'];assert model.entered.wait(1)
        session.stop();model.release.set()
        for _ in range(100):
            if session.job(key)['status']!='running':break
            time.sleep(.01)
        assert model.calls==1 and session.job(key)['status']=='stopped'
        assert not any(c[0]=='move' for c in arm.log)
    finally:session.close()

def test_hand_gate_provider_is_the_runtime_status_surface():
    from lookandpick.status_server import fake_gate_status
    class Gate:
        def status(self):return fake_gate_status(3)
        def close(self):pass
    log=CallLog();runtime=Runtime(ScriptedModel(log),arm=SimArm(),tracker=FakeTracker(),log=log,gate=Gate())
    session=RuntimeSession(runtime)
    try:assert session.hand_gate()['state']=='slow'
    finally:session.close()

def test_external_status_provider_proxies_only_loopback_and_is_display_only():
    from lookandpick.status_server import serve,fake_gate_status
    from lookandpick.deck_runtime import create_session
    server=serve(lambda:fake_gate_status(6),port=0)
    session=create_session(status_url=f'http://127.0.0.1:{server.server_port}')
    try:
        state=session.hand_gate();assert state['state']=='stop' and state['external']
        assert not session.safety.stopped  # status alone cannot control this runtime
    finally:session.close();server.shutdown();server.server_close()
    import pytest
    with pytest.raises(ValueError):create_session(status_url='https://example.com')
