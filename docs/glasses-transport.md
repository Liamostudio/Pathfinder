# Glasses transport and simulator review jobs

Implementation: 4 November 2026. Native setup and outstanding acceptance: [iPhone companion guide](../mobile/PathfinderGlasses/README.md).

```text
Adventurer HFP microphone → iPhone companion
  → authenticated HTTPS relay on laptop :8772
  → loopback Deck /api/glasses/*
  → Muse Voice Transcribe → awaiting_confirmation (90 s)
  → explicit confirm → existing Runtime → Spark → Tools / Safety → simulator
```

`glasses_gateway.py` exposes only the following native routes. Each requires `Authorization: Bearer <pairing-token>` and a matching Host header; browser Origin requests are refused. Keys are never accepted in query parameters. Requests/responses are not logged by the relay. The main Deck server remains loopback-only. Off-loopback relay binding requires TLS, certificate/key and explicit public host. Both servers bound uploads and socket read times; the relay never forwards arbitrary URLs or other Deck APIs.

| Native relay | Loopback Deck | Purpose |
| --- | --- | --- |
| `GET /v1/status` | `/api/glasses/status` | Model, scripted/live ASR, simulator-only capability |
| `POST /v1/input` | `/api/glasses/input` | Prepare a reviewed input; no robot tool call yet |
| `GET /v1/jobs/<id>` | `/api/glasses/jobs/<id>` | Poll preparation, transcript or final result |
| `POST /v1/confirm` | `/api/glasses/confirm` | Run the reviewed transcript once |
| `POST /v1/cancel` | `/api/glasses/cancel` | Discard pending review; running input invokes STOP |
| `POST /v1/stop` | `/api/glasses/stop` | Direct local STOP, independent of ASR/model queue |

Input JSON fields: `request_id` (new UUID), either `text` (1–1000 characters) or `audio_wav` (base64 WAV), optional `image_jpeg` (base64 JPEG), and `send_to_meta: true` for real audio/photo processing. Fake mode requires `demo_transcript` when audio is supplied. Real ASR must not silently use this transcript. The upload limit is 3 MiB; audio is mono 16-bit at 16/24 kHz, 0.2–30 s and ≤1,440,044 bytes; JPEG ≤400 kB. WAV format, complete PCM payload and duration are checked before a model call; JPEG checks bound size and file markers, not full decoding or camera provenance.

Input returns `{job_id,status}` (202). Poll for `awaiting_confirmation`, read `transcript`, and confirm with `{job_id}`. States are `preparing`, `transcribing`, `awaiting_confirmation`, `running`, then `completed`, `error`, `stopped`, `cancelled` or `expired`. Results use the existing Runtime result shape. Transcripts must be re-recorded after expiry; they cannot be edited in-place. New text uses a new UUID. Repeated identical `request_id` payloads return the same retained job; reusing it for different data fails. Confirmation is idempotent for retained jobs. Retry protection is bounded in-memory history, not persistent across server restarts.

All input types reserve the same `RuntimeSession.active` slot as Deck typed commands and demos. Pending media lives in memory, is never added to the job/timeline response, and is discarded on terminal states. ASR receives audio only; the photo is held until confirmation, then accompanies the normal scene and request for Spark. An empty/failed transcript runs no tool. Typed STOP bypasses upload validation; recognised spoken STOP bypasses confirmation and Spark. Contributor Spark is refused for photos. The companion's spoken response uses iPhone TTS, not a separate Meta speech-generation model.

STOP increments a generation counter and revokes every pending transcript. Late ASR results are ignored even if an operator resets afterwards. A stopped running model retains its queue reservation until its worker returns, so reset cannot clear the latch while that worker is still using the runtime. The existing tool safety checks reject any late motion request. STOP does not cancel a provider request already transmitted or refund it. This transport deliberately provides no remote reset or real-arm mode.

Tests use `SimArm`, fake trackers/models/transcription and real loopback HTTP/TLS. They cover review-before-tools, optional photo delivery, spoken/typed STOP, delayed ASR/model replies, expiry, request identity, repeated confirmation, malformed/truncated/long audio, media consent, authentication/origin/Host checks, route restriction, token file mode, TLS trust and legacy Deck rejection. Run:

```bash
workspace_splat/.venv/bin/python -m pytest tests/test_glasses_input.py tests/test_glasses_gateway.py tests/test_deck_runtime.py tests/test_voice_glasses.py -q
```

HTTP tests need a host environment that allows loopback sockets. They do not open a microphone or serial port, call Meta, or need a GPU. These tests validate transport/runtime behaviour; they do not prove native compilation, actual Adventurer audio routing or end-to-end spoken-command accuracy.
