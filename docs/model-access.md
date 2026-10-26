# Model access notes (checked 26 Oct 2026)

Secondary sources only; the official pages should be confirmed before relying on any of this.

## SAM 3.1
- Official weights: `facebook/sam3.1` on Hugging Face (gated: accept Meta's terms, authenticate). Code: `facebookresearch/sam3`.
- Weights are downloadable, so local-on-Legion is viable. Licence is Meta's SAM License; acknowledge the SAM Materials in the README.
- Unofficial mirrors exist (e.g. `unsloth/sam3.1`); prefer the official repo.
- TODO: confirm it fits in 8GB VRAM alongside the hand detector.

## Muse Voice Transcribe (`muse-voice-transcribe-1.0`)
- Reported realtime/streaming only, via WebSocket (`wss://api.meta.ai/v1/asr/realtime`); API key goes in the handshake frame, not an HTTP header. Reportedly $0.18/hour.
- File-upload endpoint: sources disagree. Design the voice adapter around streaming.
- Docs: https://developer.meta.com/ai/models/muse-voice-transcribe/

## Muse Glimmer
- Local/always-on agent model: https://developer.meta.com/ai/models/muse-glimmer/
- TODO: confirm weights, size and serving route for the MacBook.
