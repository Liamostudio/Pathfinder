"""Voice Transcribe, the glasses input and the orchestrator's photo: all tested with fakes, no network and no hardware."""
import base64
import io
import json
import math
import struct
import urllib.error
import wave

import pytest

from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.glasses import GlassesInput, command_from_glasses
from lookandpick.models.base import ChatResult, FakeLLM, ToolCall
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime
from lookandpick.voice import FakeTranscriber, VoiceError, VoiceTranscriber, cost_usd, inspect_wav
from lookandpick.world import TrackedObject


def wav(seconds=1.0, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(width); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i / 20))) * channels for i in range(int(rate * seconds))))
    return buf.getvalue()


def test_a_clip_is_checked_against_the_apis_rules_before_any_call():
    assert inspect_wav(wav(1.5)) == {"seconds": pytest.approx(1.5), "rate": 16000}
    assert inspect_wav(wav(1.0, rate=24000))["rate"] == 24000
    for bad, reason in ((b"not audio", "readable WAV"), (wav(rate=44100), "16 or 24 kHz"), (wav(channels=2), "mono"), (wav(0.05), "too short")):
        with pytest.raises(VoiceError, match=reason):
            inspect_wav(bad)
    with pytest.raises(VoiceError, match="10 minutes"):
        inspect_wav(_long_wav())
    assert cost_usd(3600) == pytest.approx(0.18)


def _long_wav():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * 601)                    # 601 s of silence
    return buf.getvalue()


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def test_the_upload_has_the_two_parts_the_api_requires_and_the_call_is_logged_with_its_cost():
    seen = {}

    def opener(request, timeout):
        seen["request"] = request
        return Reply(json.dumps({"sessionId": "s", "transcript": " pick up the red cube ", "audioDurationMs": 2000, "turns": []}).encode())
    log = CallLog()
    text = VoiceTranscriber("key", log=log, opener=opener).transcribe(wav(2.0))
    assert text == "pick up the red cube"
    request = seen["request"]
    body = request.data
    assert request.full_url == "https://api.meta.ai/v1/asr/transcribe" and request.headers["Authorization"] == "Bearer key"
    assert request.headers["Accept"] == "application/json" and request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="request"' in body and b'name="audio"' in body and b"RIFF" in body
    sent = json.loads(body.split(b"\r\n\r\n")[1].split(b"\r\n--")[0])
    assert sent["model"] == "muse-voice-transcribe-1.0" and sent["audioEncoding"] == "WAV" and sent["mode"] == "PUSH_TO_TALK" and "pick up" in sent["keywords"]
    entry = log.entries()[-1]
    assert entry["source"] == "Muse Voice Transcribe" and entry["output"] == "pick up the red cube" and entry["cost_usd"] == pytest.approx(cost_usd(2.0), abs=1e-6)


def test_failures_become_voice_errors_are_logged_and_never_leak_the_key():
    log = CallLog()

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b"{}"))

    def down(request, timeout):
        raise urllib.error.URLError("no route")
    with pytest.raises(VoiceError, match="HTTP 400"):
        VoiceTranscriber("SECRETKEY", log=log, opener=refuse).transcribe(wav())
    with pytest.raises(VoiceError, match="not reachable"):
        VoiceTranscriber("SECRETKEY", log=log, opener=down).transcribe(wav())
    with pytest.raises(VoiceError, match="unexpected"):
        VoiceTranscriber("SECRETKEY", log=log, opener=lambda r, timeout: Reply(b'{"nope": 1}')).transcribe(wav())
    assert [e["status"] for e in log.entries()] == ["error"] * 3 and "SECRETKEY" not in json.dumps(log.entries())
    with pytest.raises(ValueError):
        VoiceTranscriber("")


# ---- the photo reaches Spark, the words reach the runtime ------------------------------------------------------------------
def runtime_with(script):
    objects = FakeTracker([TrackedObject("red-cube-1", "red cube", (0.1, 0.1, 0.018)), TrackedObject("blue-cube-1", "blue cube", (0.2, -0.1, 0.018))])
    model = FakeLLM(script)
    return Runtime(model, arm=SimArm(), tracker=objects, log=CallLog(), say=lambda _: None), model


JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"


def test_the_glasses_photo_is_sent_with_the_scene_and_the_prompt_tells_the_model_to_ask_when_unsure():
    runtime, model = runtime_with([ChatResult("", (ToolCall("1", "say", '{"text": "Which one?"}'),)), ChatResult("done")])
    result = command_from_glasses(GlassesInput(image_jpeg=JPEG, text="pick up this one"), FakeTranscriber(), runtime)
    assert result.steps and result.steps[0]["name"] == "say"
    first = model.requests[0]["messages"]
    assert "smart glasses" in first[0]["content"] and "do not move" in first[0]["content"]
    content = first[1]["content"]
    assert content[0]["type"] == "text" and "red-cube-1" in content[0]["text"] and "pick up this one" in content[0]["text"]
    assert content[1]["type"] == "image_url" and base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == JPEG


def test_without_a_photo_the_message_is_plain_text_as_before():
    runtime, model = runtime_with([ChatResult("done")])
    runtime.command("what is on the table?")
    assert isinstance(model.requests[0]["messages"][1]["content"], str)


def test_spoken_words_are_transcribed_then_run_and_the_log_shows_each_model():
    runtime, model = runtime_with([ChatResult("", (ToolCall("1", "pick", '{"id": "red-cube-1"}'),)), ChatResult("Picked.")])
    transcriber = FakeTranscriber("pick up the red cube")
    result = command_from_glasses(GlassesInput(image_jpeg=JPEG, audio_wav=wav()), transcriber, runtime)
    assert transcriber.calls == 1 and result.command == "pick up the red cube" and result.steps[0]["result"] == "picked red cube"


def test_an_unusable_clip_or_silence_says_so_and_moves_nothing():
    runtime, model = runtime_with([ChatResult("never asked")])

    class Broken:
        def transcribe(self, w):
            raise VoiceError("the transcription service is not reachable")
    result = command_from_glasses(GlassesInput(audio_wav=wav()), Broken(), runtime)
    assert "voice:" in result.error and model.requests == [] and runtime.said[-1].startswith("I could not understand")
    result = command_from_glasses(GlassesInput(audio_wav=wav()), FakeTranscriber("   "), runtime)
    assert result.error == "no command heard" and model.requests == []
    assert command_from_glasses(GlassesInput(), FakeTranscriber(), runtime).error == "no command heard"


def test_a_spoken_stop_is_local_and_never_waits_for_the_photo_or_the_model():
    runtime, model = runtime_with([ChatResult("never asked")])
    result = command_from_glasses(GlassesInput(image_jpeg=JPEG, audio_wav=wav()), FakeTranscriber("Stop!"), runtime)
    assert result.reply == "stopped" and model.requests == [] and runtime.safety.stopped
