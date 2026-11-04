"""Muse Voice Transcribe: a spoken command (a WAV clip) to text, through the Meta Model API's file-upload endpoint.

    POST https://api.meta.ai/v1/asr/transcribe   multipart: `request` (JSON) + `audio` (a mono 16-bit PCM WAV at 16 or 24 kHz, at most 10 minutes)
    request = {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK", "keywords": [...]}
    reply   = {"sessionId", "transcript", "audioDurationMs", "turns": []}

The format was confirmed against Meta's reference pages and a live call (a tone returns an empty transcript). Every call is written to the call log by
name with its latency and its cost ($0.18 per hour of audio). The clip is checked locally first, so a bad file never costs a call. Spoken text is
only ever a COMMAND for the orchestrator: it goes through the same validated tools and safety layer as typed text, and a spoken "stop" is caught
locally by the runtime before any model is asked.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid
import wave

from .models.base import ModelError
from .models.calllog import CallLog
from .models.meta import BASE_URL

MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_HOUR_USD = 0.18
MAX_SECONDS, MAX_BYTES, RATES = 600.0, 32_000_000, (16000, 24000)
DEFAULT_KEYWORDS = ["Pathfinder", "pick up", "place", "stop", "cube", "mug", "next to", "this one", "that one"]


class VoiceError(ModelError):
    """The clip was unusable or the transcription call failed."""


def inspect_wav(data: bytes) -> dict:
    """Check a clip against the API's rules and return {seconds, rate}. Raises VoiceError with the reason."""
    if len(data) > MAX_BYTES:
        raise VoiceError("the audio is over 32 MB")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise VoiceError("not a readable WAV file (the glasses clip needs converting to mono 16-bit WAV at 16 kHz)") from None
    if channels != 1 or width != 2 or rate not in RATES:
        raise VoiceError(f"the WAV must be mono 16-bit at 16 or 24 kHz (this is {channels} ch, {width * 8}-bit, {rate} Hz)")
    seconds = frames / rate
    if seconds > MAX_SECONDS:
        raise VoiceError("the audio is longer than 10 minutes")
    if seconds < 0.2:
        raise VoiceError("the audio is too short to contain a command")
    return {"seconds": seconds, "rate": rate}


def cost_usd(seconds: float) -> float:
    return seconds / 3600 * PRICE_PER_HOUR_USD


class VoiceTranscriber:
    name = "Muse Voice Transcribe"

    def __init__(self, api_key: str, *, base_url: str = BASE_URL, keywords: list[str] | None = None, log: CallLog | None = None,
                 timeout_s: float = 40.0, opener=None) -> None:
        if not api_key:
            raise ValueError("an API key is required (MODEL_API_KEY in .env)")
        self._key, self.base_url, self.keywords = api_key, base_url.rstrip("/"), DEFAULT_KEYWORDS if keywords is None else list(keywords)
        self.log, self.timeout_s, self._open = log if log is not None else CallLog(), timeout_s, opener or urllib.request.urlopen

    @classmethod
    def from_env(cls, log: CallLog | None = None, env_file="./.env") -> "VoiceTranscriber":
        import os

        from .models.meta import load_env
        load_env(env_file)
        return cls(os.environ.get("MODEL_API_KEY", ""), log=log)

    def _body(self, wav: bytes) -> tuple[bytes, str]:
        boundary = uuid.uuid4().hex
        request = {"model": MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
        if self.keywords:
            request["keywords"] = self.keywords
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n{json.dumps(request)}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="command.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        return head + wav + f"\r\n--{boundary}--\r\n".encode(), boundary

    def transcribe(self, wav: bytes) -> str:
        info = inspect_wav(wav)
        body, boundary = self._body(wav)
        request = urllib.request.Request(f"{self.base_url}/asr/transcribe", data=body, headers={
            "Authorization": f"Bearer {self._key}", "Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        started = time.monotonic()
        label = f"{info['seconds']:.1f} s of speech"
        try:
            with self._open(request, timeout=self.timeout_s) as reply:
                data = json.loads(reply.read(2_000_000))
            text = str(data["transcript"]).strip()
        except urllib.error.HTTPError as exc:
            self.log.record(self.name, label, f"HTTP {exc.code}", (time.monotonic() - started) * 1000, status="error")
            raise VoiceError(f"the transcription service refused the clip (HTTP {exc.code})") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self.log.record(self.name, label, "unreachable", (time.monotonic() - started) * 1000, status="error")
            raise VoiceError("the transcription service is not reachable") from None
        except (KeyError, ValueError, TypeError):
            self.log.record(self.name, label, "unexpected reply", (time.monotonic() - started) * 1000, status="error")
            raise VoiceError("the transcription service sent an unexpected reply") from None
        self.log.record(self.name, label, text or "(no speech)", (time.monotonic() - started) * 1000, cost_usd=cost_usd(info["seconds"]))
        return text


class FakeTranscriber:
    """Returns scripted text, so tests and offline demos cost nothing."""
    name = "Fake transcriber"

    def __init__(self, text: str = "") -> None:
        self.text, self.calls = text, 0

    def transcribe(self, wav: bytes) -> str:
        self.calls += 1
        return self.text


def record_wav(seconds: float, path: str | None = None, device: str | None = None) -> bytes:
    """Record from the microphone with `arecord` (16 kHz, mono, 16-bit). Needs the microphone and Liam present; nothing is kept unless `path` is given."""
    import subprocess
    if not 0.5 <= seconds <= 30:
        raise ValueError("record between 0.5 and 30 seconds")
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(int(round(seconds))), "-t", "wav"] + (["-D", device] if device else []) + ["-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=seconds + 10, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceError(f"could not record from the microphone: {exc}") from None
    if path:
        with open(path, "wb") as handle:
            handle.write(out)
    return out
