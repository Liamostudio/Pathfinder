# Model access notes (checked 28 Oct 2026)

Read from Meta's own pages (dev.meta.ai, which developer.meta.com now redirects to); re-check pricing and limits before relying on them.

## Meta Model API (Muse Spark, Voice Transcribe)
- Base URL `https://api.meta.ai/v1`, bearer token (`Authorization: Bearer $MODEL_API_KEY`). Key creation: log in at dev.meta.ai (Authentication and Quickstart pages in the docs). Pricing and rate limits: `/docs/pricing-rate-limits` (not yet read).
- **OpenAI-compatible** (chat-completions messages array), so an OpenAI-style client or plain HTTP works. Tool calling supported (parallel and streamed): `/docs/tool-calling`. Image input: `/docs/image-understanding`.

## Muse Spark
- Model IDs: `muse-spark-1.3` (standard; calls not used to improve Meta products) and `muse-spark-1.3-contributor` (calls are used to improve products).
- Price per million tokens (input / cached input / output): standard $1.25 / $0.15 / $4.25; contributor $0.10 / $0.002 / $0.20. 1M-token context. Natively takes images, video, documents.
- Rough cost: a 2k-in/300-out orchestrator call is about $0.004 on standard, so the ~$150 credit is ample if scenes are labelled on change, not on a timer.
- Privacy: send scene JSON rather than camera frames of people where possible; use the standard model for anything containing people.
- Weights for 1.3 are not stated as open (a separate "Muse Spark Open" is mentioned).

## Muse Voice Transcribe (`muse-voice-transcribe-1.0`)
- Realtime WebSocket `wss://api.meta.ai/v1/asr/realtime` **and** file upload `POST /v1/asr/transcribe` (the earlier "streaming only" note was wrong). Turn-level timestamps, optional speaker labels, $0.18/hour. WebSocket key placement still to confirm from the realtime page.

## Muse Glimmer
- 30B, **open weights, Apache 2.0**: `huggingface.co/meta-models/Muse-Glimmer-30B`. Runs on a single consumer GPU or Mac.
- Runtimes listed: llama.cpp, vLLM, Ollama, transformers, Unsloth, LM Studio, ExecuTorch (MLX not listed). Tuned for tool calling; multimodal input implied, not explicit.
- Plan: serve on the MacBook (M1 Max, 64 GB) with Ollama or llama.cpp behind an OpenAI-compatible endpoint, so the orchestrator swaps Spark for Glimmer by changing the base URL and model name. Quantisation to use and actual speed: unchecked.

## SAM 3.1
- Official weights: `facebook/sam3.1` on Hugging Face (gated, authenticated). Code: `facebookresearch/sam3`. Downloaded and running locally (see PROGRESS.md). Licence is Meta's SAM License; acknowledge the SAM Materials in the README.
