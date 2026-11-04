# Codex brief: the hosted replay (the "live product" for the submission), 4 Nov 2026

Read `CLAUDE.md` first (safety rules are non-negotiable), then `docs/demo-2026-11-04.md` and the 4 Nov entries of `PROGRESS.md`. British English. Deadline for the whole project is 2am UK Thu 5 Nov; aim to have a working version committed by about 22:00.

## Working rules for a concurrent instance (important)
- Work in your OWN git worktree and branch: `git worktree add ../pathfinder-replay -b replay-site` from `main`. Commit only your own files. `git pull --rebase origin main` before every push. Do not push to `main` without Liam's say-so; push your branch.
- Own ONLY these paths (new files): `replay_site/`, `scripts/export_replay.py`, `tests/test_export_replay.py`, `tests/replay_site/`, `docs/replay.md`. Edit nothing else. In particular do NOT touch `lookandpick/web/` (another Codex instance is rebuilding the Deck's 3D view there), `lookandpick/tools.py`, `safety.py`, `runtime.py`, `hand_*.py`, `recovery.py`, `zones.py`, `models/*`, `adapters/*`, `README.md`, or `PROGRESS.md`.
- Log what you did and what you tested in `docs/log/2026-11-04-replay.md` (a new file; Claude merges it into `PROGRESS.md` at the end). Tick no milestone. Say plainly what was only tested with fakes.
- Do NOT touch the cameras, the GPU, the real arm, or ports 8765 to 8772 (the live demo owns them). Test servers on ports 8780 and up.
- Never commit secrets (`.env`, the glasses pairing token, TLS keys) or personal camera frames.

## What to build
A static, backend-free page that plays back ONE recorded take of the demo, so a judge can open a URL and watch what the system did, with the real timeline of model calls. It must be honest: a banner says "Recorded simulation run. The real system runs locally on a laptop with two cameras; the arm shown is simulated."

### Input data (already exists)
`GET /api/replay` on the Deck returns:
`{schema_version: 1, mode, title, frames: [{t, scene: {arm_position, objects: [{id,label,position,held}], hands: [{id,position,radius_m}]}, hand_gate: {state, scale, nearest_m, hands, fps, cameras?}, stop?, route?, joints: {shoulder_pan: {position,...}, ...}, speed_scale, stopped}], events: [{t, source, input, output, latency_ms, status, cost_usd}], limitations: [...]}`. Frames are about 25 per second. Positions are in the MARKER frame (metres, x right, y up, z up). `source` values include `Muse Voice Transcribe`, `Meta glasses`, `Muse Spark`, `Tools`, `Arm`, `Safety`, `SAM 3.1`. Saved takes will appear as `recordings/launcher/takes/take-NNN.json` (written by the launcher); until then use a replay you save yourself from a Deck on your own port with `--backend sim --model fake`.

### Deliverables
1. **`scripts/export_replay.py`**: read a take (file or `--deck-url` on loopback), validate it, and write `replay_site/data/take.json`. It must REMOVE or refuse anything that looks like a secret or personal data (keys, tokens, image bytes, file paths outside the repo), round numbers to 3 decimals, thin frames to at most 20 per second, and print a short summary (duration, events, models used, total cost). Unit-test it (a fake take, a take with a planted fake key that must be stripped or rejected, a malformed take that must give a clear error).
2. **`replay_site/index.html`** (+ plain JS/CSS, no build step, no framework, no CDN: everything local so it works offline and from any static host): play / pause / scrub, speed 0.5x/1x/2x, a top-down 2D scene (the table, the marker at the origin, the arm drawn from `joints` or `arm_position`, objects, the held object riding with the gripper, hands as translucent discs, the route polyline when present), banners for `stop` (person versus fault, recovery countdown), the hand-gate state and speed scale as a small gauge, and the timeline of model calls beside it (source name, input, output, latency, cost), highlighting the event nearest the playhead. Design it as `render(frame)` so a 3D renderer can be dropped in later (another instance is building a 3D Run view; do not wait for it).
3. **A caption strip** over the scene showing the latest spoken command (`Meta glasses` / `Muse Voice Transcribe` event), Spark's answer and the latest short arm status ("Pausing: a hand is in the way", "Going around", "Heading back to rest").
4. **`docs/replay.md`**: how to export a take, how to preview locally (`python3 -m http.server 8780 -d replay_site`), and how to host it on a static host (GitHub Pages from the folder or Netlify drop). Do NOT deploy anything yourself and do not push to `main`: publishing is Liam's decision.
5. Tests: `tests/test_export_replay.py` (Python) and a small JS test for the frame lookup and scrubbing (`tests/replay_site/*.test.mjs`; Codex's node runtime is at `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`).

## Acceptance
- Open `replay_site/index.html` from a static server with the network unplugged: it plays a take smoothly, the scrubber works, the timeline highlights follow the playhead, and the honesty banner is visible.
- A take containing a hand stop shows the stop banner and the arm pausing.
- The exporter strips a planted fake secret, and nothing in `replay_site/` references an external URL.
- It looks deliberate at 1920x1080: one type scale, good contrast, no debug text.

## Order of work (stop after any step and it still works)
exporter + sample take, then the scene playback, then the timeline and caption strip, then the banners and gauge, then polish, then docs.
