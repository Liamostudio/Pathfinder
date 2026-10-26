# Look-and-Pick — Meta Global AI Developer Hackathon

Solo entry (indie track, "Push the Frontier") by Liam. A table-mounted SO-101 robot arm that works safely around people: Meta's models see, understand and decide; a simple, checkable safety layer enforces. Models never drive the motors directly.

Live plan docs (source of truth):
- Overview: https://claude.ai/artifact/1dQav29nms1WHyaMinXbyz
- Technical plan: https://claude.ai/artifact/UWr13dvuHRQ1mDBQUnXVCa

## Hackathon rules that affect the code

- **New work only.** Everything presented must be built during the event (building period 26 Oct – 4 Nov 2026; confirmed on the kickoff call). Keep the README split into "Built during the hackathon" and "Open source we build on" (cuRobo, LeRobot, SO-101 URDF, SAM).
- **Repo must be public.** Never commit secrets: API keys live in `.env`, which is git-ignored.
- **Deadline:** 9pm EST Wed 4 Nov = **2am UK Thu 5 Nov**. Target submitting Wed evening. Features frozen 3 Nov.
- Submission needs a 3-minute demo video, the public repo and a live product (plan: hosted replay build of mission control).
- Scoring: Impact 30%, Use of Meta tooling 25%, Demo 25%, Technical depth 20%.
- Bonus targets: Best Agentic Use-Case (orchestrator with constrained tools), Best Open-Weights Build (Muse Glimmer on own hardware), Community Vote, Most Viral (X).

## Hardware

- **Legion 5** (dev + robot machine): Ubuntu 26.04 LTS dual-boot, RTX 5070 Laptop **8GB VRAM** (the real constraint), 32GB RAM. Runs cameras, SAM 3.1, cuRobo, LeRobot.
- **M1 Max MacBook Pro, 64GB:** serves Muse Glimmer (30B, ~4-bit, <20GB) locally; mission control front end.
- **Arm:** self-built SO-101 follower, 6× Feetech STS3215 (7.4V, 1:345) + URT board. **6V supply only — never above 7.4V.** Printed parts done; servos due 27–30 Oct. Set each motor ID one at a time in LeRobot before assembly.
- Two fixed webcams (~90° apart) + ArUco marker at the arm base. Action cams available for filming tests.

## Architecture

Shared world state; no model calls another; fast loop overrides everything.

| Loop | Rate | Runs |
| --- | --- | --- |
| Fast | 15–30 Hz | SAM 3.1 tracking + lightweight hand detector, hand-vs-path check → slow/pause |
| Medium | 1–2 Hz | Triangulation, cuRobo map update |
| Slow | On events | Muse Spark labelling/parsing, heavier 3D models |

Target abstraction: treat the goal as "something the arm acts on", not "something to grasp" — pick and camera-framing are just different end routines.

## Meta models — where they run

| Model | Job | Where |
| --- | --- | --- |
| SAM 3.1 | Text-prompted tracking of hands, people, objects | Legion (local) — confirm weights are downloadable, not API-only |
| Muse Voice Transcribe | Spoken commands | Meta Model API |
| Muse Spark | Parse instructions, resolve "this one", label, narrate; orchestrator via tools `get_scene()`, `pick(id)`, `place(id, loc)`, `stop()`, `say(text)` | Meta Model API |
| Muse Glimmer | Offline orchestrator ("works with the internet unplugged") | MacBook Pro (local) |
| Muse Wonder | Occasional "director" planning / re-planning (expensive, always reasons) | API, sparingly |
| Muse Image | Previs: edit current frame into "what I'm about to do" | API (stretch) |

Priority: **Must** SAM 3.1, Voice Transcribe, Muse Spark → **Should** Glimmer → **Stretch** Wonder, Muse Image, V-JEPA 2.
Label every model call by name in the mission control timeline (input, output, latency).
Label scenes on change, not on a timer, to protect the $150 API credits. Spend limit is set.

## Open decision

Hero demo is **pick-and-place** for now. Filming/B-roll (arm holding an action cam, following a language-described target) may become the hero after a 30-minute motion test on the real arm: slow sweep smoothness (stabilisation off), repeatability of one move ×5, payload at full reach. Don't restructure around filming until that test is done.

## Safety rules for this codebase (non-negotiable)

- Safety-critical checks run locally and never depend on the network.
- Prefer speed-and-separation (slow as people approach, stop at minimum distance) over hard pauses.
- "Stop" bypasses everything.
- **Never command the real arm from a remote/unattended session.** Sim, replays and code are fine remotely; anything that powers the servos needs Liam physically present with the 6V kill switch in reach.

## Build order

Cameras + calibration → safe zones with sim arm → language to target → real arm swap-in (from ~31 Oct) → hand slowdown + polish → video + submit.
Cut lines, in order: multi-step tasks, obstacle rerouting, target confirmation, second camera. Never cut: hand safety, one camera, one clean pick.
Stub every adapter with fake data first; develop against recorded camera footage; swap in real models one at a time.

## Progress tracking

`PROGRESS.md` at the repo root is the shared tracker for Liam and any Claude session, and evidence for judges of what was built 26 Oct – 4 Nov 2026. Update it at the end of every working session and before every push, in the same commit as the work. Tick a milestone only when its acceptance test has passed and say how it was tested; be honest about half-working things. Keep it under ~200 lines (summarise older log entries to one line per day).

## Working style

British English in docs and UI text. Liam has ~3 focused hours Tue–Fri afternoons plus evenings — favour small, working increments over big rewrites.
