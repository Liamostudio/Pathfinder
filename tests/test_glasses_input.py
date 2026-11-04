import base64
import io
import threading
import time
import uuid
import wave
import pytest
from lookandpick.adapters.fake import FakeTracker,SimArm
from lookandpick.deck_runtime import RuntimeSession,ScriptedModel
from lookandpick.glasses_input import GlassesInputs
from lookandpick.models.base import ChatResult,FakeLLM,ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime


def wav(seconds=1):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*int(16000*seconds))
    return base64.b64encode(out.getvalue()).decode()

@pytest.fixture
def session():
    log=CallLog();runtime=Runtime(ScriptedModel(log),arm=SimArm(),tracker=FakeTracker(),log=log,say=lambda _:None)
    value=RuntimeSession(runtime)
    yield value
    value.close()

def wait(s,key,phases):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        job=s.job(key)
        if job['status'] in phases:return job
        time.sleep(.005)
    raise AssertionError(s.job(key))

def payload(**values):return {'request_id':str(uuid.uuid4()),**values}

def test_audio_is_reviewed_before_any_tool_call_and_confirmation_is_idempotent(session):
    c=GlassesInputs(session)
    p=payload(audio_wav=wav(),demo_transcript='pick up the red cube')
    key=c.submit(p)['job_id'];job=wait(session,key,{'awaiting_confirmation'})
    assert job['transcript']=='pick up the red cube' and session.arm.log==[]
    assert c.submit(p)['job_id']==key
    c.confirm(key);job=wait(session,key,{'completed'})
    assert job['result']['steps'][0]['result']=='picked red cube'
    calls=len(session.runtime.model.requests)
    c.confirm(key);assert len(session.runtime.model.requests)==calls
    assert not c.pending

def test_stop_discards_late_transcript_even_after_laptop_reset(session):
    entered=threading.Event();release=threading.Event()
    class Waiting:
        def transcribe(self,a):entered.set();release.wait(2);return 'pick up the red cube'
    c=GlassesInputs(session,transcriber_factory=Waiting)
    key=c.submit(payload(audio_wav=wav(),demo_transcript='unused'))['job_id'];assert entered.wait(1)
    session.stop();assert session.active is None
    session.safety.reset();release.set();assert wait(session,key,{'stopped'})['status']=='stopped'
    assert c.confirm(key)['status']=='stopped' and not session.runtime.model.requests

def test_stop_word_bypasses_invalid_media_and_a_busy_queue(session):
    c=GlassesInputs(session);key=c.submit(payload(text='pick the red cube'))['job_id']
    wait(session,key,{'awaiting_confirmation'})
    assert c.submit({'text':'STOP!','audio_wav':'not base64'})['status']=='stopped'
    assert session.safety.stopped and not session.runtime.model.requests

def test_pending_review_expires_and_retry_identity_cannot_change(session):
    c=GlassesInputs(session,review_seconds=.03);p=payload(text='pick the red cube')
    key=c.submit(p)['job_id'];wait(session,key,{'expired'});assert session.active is None
    with pytest.raises(ValueError,match='different input'):c.submit({**p,'text':'place red cube'})

def test_audio_consent_and_duration_are_checked_locally():
    log=CallLog();model=FakeLLM([ChatResult('unused')]);s=RuntimeSession(Runtime(model,arm=SimArm(),tracker=FakeTracker(),log=log))
    try:
        c=GlassesInputs(s)
        with pytest.raises(ValueError,match='sent to Meta'):c.submit(payload(audio_wav=wav()))
        with pytest.raises(ValueError):c.submit(payload(audio_wav='bad'))
        assert not model.requests and not s.arm.log
    finally:s.close()

def test_long_or_truncated_audio_never_reaches_a_model(session):
    c=GlassesInputs(session)
    with pytest.raises(ValueError,match='30 seconds'):c.submit(payload(audio_wav=wav(31),demo_transcript='pick red cube'))
    clip=base64.b64decode(wav())[:-100]
    with pytest.raises(ValueError,match='incomplete'):c.submit(payload(audio_wav=base64.b64encode(clip).decode(),demo_transcript='pick red cube'))
    assert not session.runtime.model.requests and session.active is None

def test_spoken_stop_needs_asr_but_skips_confirmation_and_spark(session):
    c=GlassesInputs(session)
    key=c.submit(payload(audio_wav=wav(),demo_transcript='stop'))['job_id']
    wait(session,key,{'stopped'})
    assert session.safety.stopped and not session.runtime.model.requests

def test_optional_photo_is_only_sent_to_the_orchestrator_after_confirmation(session):
    c=GlassesInputs(session)
    key=c.submit(payload(text='pick up the red cube',image_jpeg=base64.b64encode(b'\xff\xd8test\xff\xd9').decode()))['job_id']
    wait(session,key,{'awaiting_confirmation'});assert not session.runtime.model.requests
    c.confirm(key);wait(session,key,{'completed'})
    content=session.runtime.model.requests[0][1]['content']
    assert content[1]['image_url']['url'].startswith('data:image/jpeg;base64,')

def test_stopped_inflight_model_retains_queue_until_it_unwinds(session):
    entered=threading.Event();release=threading.Event()
    class Pending:
        name='pending model'
        def chat(self,*a):entered.set();release.wait(2);return ChatResult(tool_calls=(ToolCall('1','pick','{"id":"obj1"}'),))
    session.runtime.model=session.runtime.orchestrator.model=Pending()
    c=GlassesInputs(session);key=c.submit(payload(text='pick the red cube'))['job_id']
    wait(session,key,{'awaiting_confirmation'});c.confirm(key);assert entered.wait(1)
    session.stop();assert session.active==key
    with pytest.raises(ValueError,match='Wait'):session.action({'action':'reset'})
    release.set()
    deadline=time.monotonic()+1
    while session.active and time.monotonic()<deadline:time.sleep(.01)
    assert session.active is None and not any(x[0]=='move' for x in session.arm.log)
