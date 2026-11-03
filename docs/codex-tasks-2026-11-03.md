# Codex tasks, 3 Nov evening to the freeze (midday Wed 4 Nov). Claude is building the language-to-target path in parallel.

Rules unchanged: `CLAUDE.md` safety rules are non-negotiable; **Deck never powers or moves the real arm**; commit only your own files; update
`PROGRESS.md` honestly; tick nothing without a passed test. Run everything with `scripts/test_all.sh` (it says plainly if `node` is missing).
Context: `docs/integration-2026-11-03.md`, `docs/codex-brief-real-arm-deck-2026-11-02.md` (real-arm panel, trace viewer, unification tasks).

**Claude owns, do not edit:** `lookandpick/adapters/*`, `lookandpick/kinematics.py`, `lookandpick/workspace_bridge.py`, `lookandpick/models/*`,
`lookandpick/runtime.py`, `lookandpick/live_hands.py`, `lookandpick/hand_*.py`, `lookandpick/calibration.py`, `intrinsics.py`, `calibration/`, `touches/`,
`scripts/so101_*.py`, and what Claude is writing now: `scripts/sam_worker.py`, `lookandpick/adapters/sam_*.py`, `lookandpick/scene_labeller.py`.

Priority order. Do 1 first; it gates everyone.

1. **Commit your own work in clear commits** (`gaussian_studio/`, `lookandpick/workspaces/`, `lookandpick/web/*`, `lookandpick/gpu.py`, `sam_web/`, `sam_prompt.py`,
   docs, tests, and your edits to README.md, pyproject.toml, mission_control.py, app.js, index.html). Leave Claude's files alone. README: "Built during the
   hackathon" vs "Open source we build on" (MapAnything, splat trainer, MediaPipe, EfficientDet-Lite0, cuRobo, LeRobot, SO-101 URDF, SAM) with licences;
   check the cached MapAnything weights' licence and say what you found. Acceptance: `git status` shows none of your files untracked; tests pass.

2. **Deck on the real runtime (sim backend).** Add `--backend sim` to `lookandpick.mission_control` that drives `lookandpick.runtime.Runtime` with a
   `SimulationClient` arm and tracker. Contract (do not change it; ask Claude if you need more): `Runtime.command(text) -> RunResult` (blocks until the run
   ends; run it on a worker thread), `Runtime.stop() -> str` (always works, bypasses the model), `Runtime.reset()`, `Runtime.timeline() -> list[dict]`
   with Deck's event keys `{id, time, source, input, output, latency_ms, status, scripted}` plus `cost_usd` and `tokens`. Add `POST /api/command {text}`,
   `POST /api/stop`, `GET /api/timeline?since=<id>`, a text box and a big STOP button (STOP must work even while a command is running), and a timeline
   that shows model name, latency, cost and tokens per call. Acceptance: typed "pick up the red cube and put it next to the blue cube" moves the sim arm
   (needs `.env` with the Meta key; with `--model fake` it must still run a scripted path), STOP interrupts mid-move, no model call after STOP.
   New: the runtime can now look through the cameras: `lookandpick.runtime.sam_scene_from_sim(model, log, sim_url, camera)` returns `(scene, tracker)`
   (Spark names the objects, SAM 3.1 finds them; needs `scripts/sam_worker.py` running, which holds the GPU lease, so stop it before reconstruction).
   Pass them as `Runtime(..., tracker=tracker, scene=scene)`. `python -m lookandpick.runtime --scene sam "..."` shows it working. In Deck, add a
   'Look' toggle for this (default off, so the demo still works if the worker is down), and show SAM and Spark labelling calls in the timeline.

3. **Hand-gate panel.** (Claude has built the provider: `lookandpick.hand_gate.HandGate.status()` returns exactly this shape, `Runtime.hand_gate_status()`
   exposes it, `python -m lookandpick.runtime --hand-gate CAM --status-port 8768` serves `GET /api/hand-gate` and `GET /api/timeline?since=` read-only on loopback,
   and `python -m lookandpick.status_server --fake` serves a cycling fake for UI work with no camera. Your `RuntimeSession(hand_gate=callable)` can take
   `runtime.hand_gate_status` directly.) Render `GET /api/hand-gate` (Claude will provide it from the live hand loop; until then build against this fixed shape and a fake):
   `{"state": "clear|slow|stop|latched|stale", "scale": 0.0-1.0, "nearest_m": float|null, "hands": int, "fps": float, "latency_ms": {"p50": float,
   "p95": float}, "stale_s": float, "updated": epoch_s}`. Show a state chip, the speed scale as a bar, nearest-hand distance, fps and latency, and "STALE"
   in red when `stale_s` exceeds 0.15. Acceptance: a fake feed cycling through every state renders each one; a missing endpoint shows NOT CONNECTED.

4. **Hosted replay for the submission ("live product").** An exporter that records one sim run (arm trajectory, events, the model-call timeline, hand-gate
   states) to one JSON file, and a static page (no Python, no server code) that plays it back with the schematic, the timeline and a scrubber. It must work
   from any static host and offline from a folder. Put the recorded demo in `replay/`. Acceptance: open `replay/index.html` via `python3 -m http.server`
   and play the run end to end; a corrupted file shows an error, not a blank page.

5. **Real-arm read-only panel and trace viewer** (`docs/codex-brief-real-arm-deck-2026-11-02.md`, sections "What to build" 1 to 5).

6. **Robot placement from the measured base frame** (brief, "Unification tasks" 2 to 4): use `lookandpick.workspace_bridge.robot_placement` and
   `urdf_joint_radians`; measure the black marker square with Liam and use it.

7. **Demo script for filming.** A "Run demo" button in Deck that plays a deterministic scenario on the sim (seeded): pick and place, a hand crossing the
   path (arm slows and stops, then resumes), STOP once. Everything labelled in the timeline. Acceptance: three runs in a row produce the same
   trajectory and the same event order.

Stop at the freeze (midday Wed): after that only bug fixes. Report in `PROGRESS.md` what passed, how it was tested and what did not work.
