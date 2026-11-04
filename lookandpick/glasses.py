"""Meta glasses in: what the user is looking at, and what they said, turned into one safe command for the arm.

    glasses photo (first-person view) + spoken or typed words --> Muse Voice Transcribe --> Muse Spark (sees the photo + the scene) --> validated tool calls

The glasses are an INPUT: a photo of the user's view and their voice. Spark resolves "this one" against the scene list using the photo and can
only answer with the same five validated tools as always (so it can never reach the motors), and the safety layer, the hand gate and the local
stop word still sit in front of every move. If the photo does not say which object is meant, the model is told to ask, not to guess.
Privacy: a first-person photo may contain people, so use the standard (non-contributor) Spark model; nothing is stored here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models.orchestrator import STOP_WORDS, RunResult
from .voice import VoiceError


@dataclass(frozen=True)
class GlassesInput:
    image_jpeg: bytes | None = None      # the glasses' first-person photo
    audio_wav: bytes | None = None       # the spoken request, mono 16-bit WAV at 16 or 24 kHz
    text: str | None = None              # or typed words (a fallback; also what a transcript becomes)
    source: str = "Meta glasses"


def command_from_glasses(item: GlassesInput, transcriber, runtime) -> RunResult:
    """Words (transcribed if spoken) plus the view, through the runtime. Never raises for a bad clip: it says so and does not move."""
    text = (item.text or "").strip()
    if not text and item.audio_wav:
        try:
            text = transcriber.transcribe(item.audio_wav).strip()
        except VoiceError as exc:
            runtime._speak(f"I could not understand that: {exc}.")
            return RunResult("", reply="I could not understand that.", error=f"voice: {exc}")
    if not text:
        runtime._speak("I did not hear a command.")
        return RunResult("", reply="I did not hear a command.", error="no command heard")
    if STOP_WORDS.match(text):
        return runtime.command(text)                      # a spoken stop is local, instant and needs no photo
    return runtime.command(text, item.image_jpeg)
