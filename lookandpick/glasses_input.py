"""Bounded, review-before-run glasses/voice jobs sharing Deck's command and STOP latch."""
from dataclasses import asdict
import base64
import hashlib
import io
import json
import re
import threading
import time
import wave
from uuid import uuid4

from .models.orchestrator import STOP_WORDS
from .voice import FakeTranscriber, VoiceTranscriber, VoiceError, inspect_wav

MAX_BODY = 3 * 1024**2
TERMINAL = {'completed', 'error', 'stopped', 'cancelled', 'expired'}


def decode(value, limit, label):
    if value is None: return None
    if not isinstance(value, str) or len(value) > (limit + 2)//3*4:
        raise ValueError(f'{label} exceeds its upload limit')
    try: data = base64.b64decode(value, validate=True)
    except ValueError: raise ValueError(f'Invalid {label} encoding') from None
    if not data or len(data) > limit: raise ValueError(f'Invalid {label} size')
    return data


def validate_input(payload):
    if not isinstance(payload, dict): raise ValueError('Expected an input object')
    key = payload.get('request_id')
    if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', key):
        raise ValueError('Supply a unique request_id (UUID) for retry protection')
    text = payload.get('text', '')
    if not isinstance(text, str) or len(text) > 1000: raise ValueError('Use text under 1,000 characters')
    audio = decode(payload.get('audio_wav'), 1_440_044, 'audio')
    photo = decode(payload.get('image_jpeg'), 400_000, 'photo')
    if audio:
        try: info=inspect_wav(audio)
        except VoiceError as exc: raise ValueError(str(exc)) from None
        if info['seconds'] > 30: raise ValueError('Record at most 30 seconds')
        with wave.open(io.BytesIO(audio),'rb') as clip:
            frames=clip.getnframes()
            if len(clip.readframes(frames)) != frames*2:raise ValueError('The WAV recording is incomplete')
    if photo and not (photo.startswith(b'\xff\xd8') and photo.endswith(b'\xff\xd9')):
        raise ValueError('Attach a JPEG photo')
    if not text.strip() and not audio: raise ValueError('Speak or type a command first')
    return key, text.strip(), audio, photo


class GlassesInputs:
    def __init__(self, session, *, transcriber_factory=None, review_seconds=90):
        self.session = session
        self.factory = transcriber_factory
        self.review_seconds = review_seconds
        self.pending = {}
        self.requests = {}
        self.timers = {}
        self.latest = None
        session.glasses_inputs = self

    @property
    def offline(self): return self.session.runtime.model.name.startswith('Scripted model')

    def status(self):
        return {'available': True, 'backend': 'sim', 'simulator_only': True,
                'model': self.session.runtime.model.name,
                'voice_mode': 'scripted' if self.offline else 'Meta Muse Voice Transcribe',
                'review_required': True, 'max_audio_seconds': 30, 'latest_job_id': self.latest,
                'stop_generation': self.session.stop_generation}

    def submit(self, payload):
        # Typed STOP remains independent of clip validation, queue state and all model services.
        text = payload.get('text', '') if isinstance(payload, dict) else ''
        if isinstance(text, str) and STOP_WORDS.match(text):
            self.session.stop(); return {'status': 'stopped'}
        request_id, text, audio, photo = validate_input(payload)
        if not self.offline and (audio or photo) and payload.get('send_to_meta') is not True:
            raise ValueError('Confirm that this recording/photo may be sent to Meta')
        if photo and 'contributor' in getattr(self.session.runtime.model, 'model', ''):
            raise ValueError('Use the standard Spark model for personal photos')
        demo = payload.get('demo_transcript', '')
        if not isinstance(demo, str) or len(demo) > 1000: raise ValueError('Invalid scripted transcript')
        if self.offline and audio and not text and not demo.strip():
            raise ValueError('Scripted mode needs a demo transcript; it does not recognise speech')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        s = self.session
        with s._lock:
            if request_id in self.requests:
                previous_digest, key = self.requests[request_id]
                if previous_digest != digest: raise ValueError('request_id was already used for different input')
                if key not in s.jobs:raise ValueError('Input history expired; use a new request_id')
                return {'job_id': key, 'status': s.jobs[key]['status']}
            if s.active: raise ValueError('A command is already running; STOP remains available')
            if s.safety.stopped: raise ValueError('Stop is latched; reset at the laptop first')
            key = uuid4().hex
            s.active = key; s._revision += 1
            s.jobs[key] = {'id': key, 'kind': 'glasses', 'status': 'transcribing' if audio and not text else 'preparing',
                           'source': 'Meta Adventurer / iPhone', 'command': text}
            self.requests[request_id] = (digest, key); self.latest = key
            self.pending[key] = {'generation': s.stop_generation, 'photo': photo, 'created': time.monotonic()}
            while len(self.requests) > 100:
                _, (_, old) = self._pop_oldest()
                if old != s.active: s.jobs.pop(old, None)
        threading.Thread(target=self._prepare, args=(key, text, audio, demo), daemon=True, name='glasses-transcript').start()
        return {'job_id': key, 'status': s.jobs[key]['status']}

    def _pop_oldest(self):
        first = next(iter(self.requests)); return first, self.requests.pop(first)

    def _valid(self, key):
        data = self.pending.get(key)
        return data and data['generation'] == self.session.stop_generation and not self.session.safety.stopped

    def _finish(self, key, status, **fields):
        s = self.session
        with s._lock:
            if key in s.jobs and s.jobs[key]['status'] not in TERMINAL:
                s.jobs[key].update(status=status, **fields)
            self.pending.pop(key, None)
            timer = self.timers.pop(key, None)
            if timer: timer.cancel()
            if s.active == key: s.active = None; s._revision += 1

    def _prepare(self, key, text, audio, demo):
        try:
            if not self._valid(key): return self._finish(key, 'stopped')
            if audio and not text:
                factory = self.factory or (lambda: FakeTranscriber(demo) if self.offline else VoiceTranscriber.from_env(self.session.runtime.log))
                text = factory().transcribe(audio).strip()
            if not self._valid(key): return self._finish(key, 'stopped')
            if not text: raise ValueError('No command was heard. Try again.')
            if STOP_WORDS.match(text):
                self.session.stop(); return self._finish(key, 'stopped', transcript=text)
            if len(text) > 1000: raise ValueError('The transcript is too long')
            with self.session._lock:
                if not self._valid(key): return self._finish(key, 'stopped')
                self.pending[key]['text'] = text
                self.pending[key]['expires'] = time.monotonic() + self.review_seconds
                self.session.jobs[key].update(status='awaiting_confirmation', transcript=text, command=text,
                                              review_seconds=self.review_seconds, has_photo=bool(self.pending[key]['photo']))
                timer = threading.Timer(self.review_seconds, lambda: self._expire(key)); timer.daemon = True
                self.timers[key] = timer; timer.start()
            self.session.runtime.log.record('Meta glasses', 'transcript ready / awaiting confirmation', text, 0)
        except Exception as exc: self._finish(key, 'error', error=str(exc))

    def _expire(self, key):
        with self.session._lock:
            if self.session.jobs.get(key, {}).get('status') == 'awaiting_confirmation':
                self._finish(key, 'expired', error='Review expired; record the instruction again')

    def confirm(self, key):
        s = self.session
        with s._lock:
            if key not in s.jobs or s.jobs[key].get('kind') != 'glasses': raise ValueError('Unknown glasses input')
            if s.jobs[key]['status'] in TERMINAL or s.jobs[key]['status'] == 'running':
                if key not in s.jobs:raise ValueError('Input history expired; use a new request_id')
                return {'job_id': key, 'status': s.jobs[key]['status']}
            if s.jobs[key]['status'] != 'awaiting_confirmation': raise ValueError('Wait for the transcript')
            if not self._valid(key): self._finish(key, 'stopped'); raise ValueError('Stopped input cannot be resumed')
            if time.monotonic() >= self.pending[key]['expires']:
                self._finish(key, 'expired'); raise ValueError('The reviewed input expired')
            timer = self.timers.pop(key, None)
            if timer: timer.cancel()
            s.jobs[key]['status'] = 'running'; data = dict(self.pending[key])
        threading.Thread(target=self._execute, args=(key, data), daemon=True, name='glasses-command').start()
        return {'job_id': key, 'status': s.jobs[key]['status']}

    def _execute(self, key, data):
        try:
            if not self._valid(key): return self._finish(key, 'stopped')
            result = self.session.runtime.command(data['text'], data['photo'])
            self._finish(key, 'stopped' if not self._valid(key) else 'error' if result.error else 'completed', result=asdict(result))
        except Exception as exc:
            self.session.stop(); self._finish(key, 'error', error=str(exc))

    def cancel(self, key):
        with self.session._lock:
            if self.session.jobs.get(key, {}).get('kind') != 'glasses': raise ValueError('Unknown glasses input')
            running = self.session.jobs[key]['status'] == 'running'
            if not running: self._finish(key, 'cancelled')
        if running: self.session.stop()
        return self.session.job(key)

    def stop_all(self):
        # Called after STOP's generation increment, before any late transcript can become executable.
        with self.session._lock:
            for key in list(self.pending):
                if self.session.jobs[key]['status']=='running':
                    self.pending.pop(key,None)
                    self.session.jobs[key]['status']='stopped'
                    # Retain the active reservation until the model/motion worker unwinds.
                else:self._finish(key,'stopped')
