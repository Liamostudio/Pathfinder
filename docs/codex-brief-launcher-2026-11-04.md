# Codex brief: the Pathfinder launcher (4 Nov 2026, evening)

Read first: `CLAUDE.md` (safety rules are non-negotiable), `docs/demo-2026-11-04.md` (run of show, what each model does), `PROGRESS.md` (the 4 Nov entries), `docs/local-workspaces.md`. Commit only your own files; pull (`git pull --rebase --autostash`) before every commit; add a PROGRESS entry saying what was tested and how; tick no milestone; British English.

## Why

Starting the demo is currently a pile of manual steps that only Claude knows (start order, GPU juggling, camera indices, resets). For the film Liam needs ONE app that takes stock of the hardware, does all of those jobs, then opens the arm workspace demo page. He will drive it with clicks; nobody should type a command.

## Corrections from the audit (4 Nov evening). These OVERRIDE anything below that disagrees

1. **Both demo cubes start inside no-go cubes (this breaks filming).** On the active workspace `ws-380f51eac8164bda` v0003 (18 blocked cells) the red cube's start (0.23, 0.10) hits cube 3,8,1 and the blue cube's (0.22, -0.13) hits cube 3,3,1. A simulator reset puts objects back at these spots (`sim_env/model.py`), so every Reset scene would land them where picks are refused. Fix: **presets elsewhere**, applied after every reset. `nudge` moves an object at most 10 cm per axis and `place_prop` refuses the base objects, so add a simulator action `place` (name, x, y, optional yaw; only while idle; reject positions outside the table) with a test, and have Reset scene call reset THEN place for each preset. The preflight warning stays (it must check the PRESET positions, not the model defaults). Liam may instead redraw the cubes; the warning covers both.
2. **Stopping Deck does not stop a reconstruction.** The worker is started detached (`start_new_session=True`, `workspaces/service.py`), so killing Deck's process group leaves it running and holding the GPU lock. Go live step 1 and Stop all must FIRST cancel the job: `POST /api/workspace-jobs/<run>/cancel` (list the runs from the workspace API), then wait for the lock to be free.
3. **The hand-gate health check can pass on fake data.** Without `--hand-gate`, `/api/hand-gate` answers with simulated gate data (`deck_runtime.simulated_gate`). The check must require a `cameras` key with TWO entries, each with a small `age_s` (under about 0.3), AND fps above 15.
4. **SAM source names.** The main camera's source prefix is `sam-people` (sources `sam-people-person` and `sam-people-hand`); the extra camera adds its name (`sam-people-second-person`, ...). Match on the `sam-people` prefix. Voxel `sources` already carry `age_ms`.
5. **The GPU lock file never says it is free.** `recordings/.gpu.lock` keeps the last owner's pid after that process dies, so reading it proves nothing. Test by trying a non-blocking `fcntl.flock(LOCK_EX | LOCK_NB)` on it (see `lookandpick/gpu.py`) and releasing at once.
6. **Setup does not end at "Ready".** A job state of `ready` only means ready to review and save. The workspace becomes active when it is SAVED and `recordings/workspaces/active.json` changes (`repository.activate`). Light the Setup stage on that change (and show the job state as a sub-status).

Smaller points: the camera adapter is `lookandpick/adapters/live_camera.py`. **Interpreter:** run the launcher under `workspace_splat/.venv` or `~/src/hands-venv` (both import `cv2`, `detect_marker` and the zone code; the system Python has no `cv2`); `pytest` exists only in `workspace_splat/.venv`. Deck runs fine under `hands-venv` (the one piece that needs scipy, `compose.py`, runs as its own subprocess under `workspace_splat/.venv`, and Deck starts the reconstruction worker with its own Python), but Setup should still use `workspace_splat/.venv` as below. **Services already running that the launcher did not start** (today: the simulator on 8766, the glasses relay on 192.168.1.142:8772 and a scripted Deck on 8774): adopt them if healthy; for any process the launcher did not start, Stop signals THAT ONE PID only, never its process group (it may be a terminal's), and asks first. **Self-check clash:** `scripts/sim_motion_check.py` talks straight to the simulator, resets it and has no zones; if a live hand gate latches a stop during it the check fails for the wrong reason. Enable it only BEFORE Go live (or with Deck stopped), then Reset. **Endpoints the launcher must NEVER call:** Deck's `POST /api/real-arm/connect` and `/api/real-arm/disconnect` (read-only telemetry, but they open the arm's serial port).

## What to build

A small **launcher**: a separate stdlib-first process with its own single page (it must start and stop Deck itself, so it cannot live inside Deck).

- New files only: `lookandpick/launcher/` (supervisor, preflight, config), `lookandpick/launcher/web/` (one page, plain HTML/CSS/JS, no build step), `scripts/pathfinder_launch.sh` (one command that starts the launcher and opens it in the browser), `tests/test_launcher.py`, `docs/launcher.md`.
- Serves on **127.0.0.1:8760** only (reject non-loopback `Host`, same pattern as `scripts/sam_worker.py::_local`). Bodies bounded. No endpoint runs arbitrary commands: services come from a fixed whitelist in config, started with `subprocess.Popen([...], start_new_session=True)` (no `shell=True`), stopped with `os.killpg`. **Never use `pkill -f` patterns** (they match their own command line and kill the caller).
- Per-service log files under `recordings/launcher/` (git-ignored already). Never write the API key, the glasses pairing token or any secret to a log or to the page.

## The page: five stages, top to bottom, each with a status light and a button

### 1. Hardware check (runs on load and on a Re-check button)
Each line is pass / warn / fail with a one-line fix:
- **Cameras, resolved by NAME not index** (indices change when a cable is re-seated; the Ace Pro dropped off USB twice today). Names from `/sys/class/video4linux/video*/name` or `/dev/v4l/by-id/`: `Insta360 Ace Pro` (capture node = index 0 of its four nodes, today `/dev/video2`) and `UGREEN Camera` (today `/dev/video6`). The built-in `Integrated Camera` is NOT used. For each: open it, read one frame at its working mode, show a small thumbnail, close it. Ace Pro: MJPG 640x360 (USB 2). UGREEN: MJPG **1280x720** (`lookandpick/adapters/live_camera.py` already forces MJPG). Also detect the ArUco marker in each thumbnail (`lookandpick.calibration.detect_marker`) and say so ("marker found" / "marker NOT in view"). Fail if a camera is missing or delivers no frame; say which and "re-seat the cable".
- **Lens files** exist and match: `calibration/ace-pro.json` (640x360), `calibration/ugreen.json` (1280x720), via `lookandpick.intrinsics.Intrinsics.load(...).size`.
- **GPU**: `nvidia-smi` shows enough free memory (SAM worker needs about 2.3 GB resident; the card is 8 GB) and the lock is really free (non-blocking `flock`, correction 5; the file's contents prove nothing).
- **Ports free**: 8765 Deck, 8766 simulator, 8767 SAM worker, 8772 glasses relay. If one is held, show which process and offer Stop.
- **Secrets present, never shown**: `.env` exists and has `MODEL_API_KEY` (say "present", not the value). Weights present: `weights/hand_landmarker.task`, the SAM checkpoint (see `scripts/sam_worker.py`), the MediaPipe EfficientDet file if used.
- **Network**: laptop Wi-Fi IPv4 (`ip -4`, today 192.168.1.142), and whether `api.meta.ai` is reachable. Say clearly: Spark and Voice need the internet, the safety layer never does.
- **Microphone** present (`arecord -l`), for the voice route.
- **Active workspace**: id and version from `recordings/workspaces/active.json`, number of manual no-go cubes, and a WARNING if a demo object (the simulator's red or blue cube) sits inside a no-go cube (use the real `ManualZoneGuard` from `lookandpick/zones.py` via `from_areas`; this is what silently blocked every pick today).
- **Manual safety tick-boxes** (cannot be detected): "Real arm: 5V supply unplugged / kill switch in reach", "Hands out of both camera views". The launcher never enables a stage past a required unticked box.

### 2. Setup (scene ingest with the glasses)
- Starts the simulator bridge (8766) and Deck (8765) **without** SAM and without the camera gate, so the GPU is free for the reconstruction.
- Opens Deck's workspace page in a new tab with plain instructions: wear the glasses, record a slow 20 to 30 s walk-around including the marker, get the clip to the laptop, upload, sample about 1 fps with a modest frame budget, reconstruct, align to the marker, draw the no-go cubes, save.
- Polls the workspace API and shows the job's stage; the stage lights up when the workspace is SAVED and becomes active (`active.json` changes; correction 6). Offer "Use the existing workspace" (skip ingest) as an equal option; it is the fallback if the glasses footage does not reconstruct.

### 3. Go live
In this order, with a progress line for each step and a timeout and a plain failure message for each:
1. Cancel any running reconstruction (`POST /api/workspace-jobs/<run>/cancel`), wait for it to exit, then confirm the GPU lock is really free with a non-blocking `flock` (correction 5).
2. Start `scripts/sam_worker.py` and wait for `GET 127.0.0.1:8767/health` to say `loaded: true` (about 15 s).
3. Stop Deck, then start it with both cameras, the hand gate, SAM people and the chosen model (the exact command is below). Show a **"Hands out of both views"** countdown while it calibrates from the marker (about 10 s); a failure prints Deck's own message ("marker seen in 0 frames", "Cannot open camera ...").
4. Health gate: `GET /api/hand-gate` has a `cameras` key with two entries, each with a small `age_s`, and fps above 15 (correction 3); `/api/workspaces/<id>/voxels` lists `hand-gate` and `sam-people*` sources once a hand or person is in view (correction 4); SAM worker healthy.
5. Open Deck's arm workspace demo page in a new tab (the launcher's job is done there).

Toggles: **Model** `fake` (rehearsal, free) / `spark` (film; standard model, not contributor, because people are in frame). Read the current `--model` choices from `--help`: another agent is adding Muse Spark Open to that flag. **SAM people** on/off (if the GPU is missing). **Second camera** on/off.

### 4. Show controls (the part Liam will use between takes)
- **Reset scene** (one click): `POST /api/action {"action":"reset"}` on Deck (the Runtime reset clears every latch, builds a fresh gate and ends at the Rest pose), then put the simulator's demo objects at the **preset positions** in the launcher config (they must be clear of the no-go cubes; check the PRESETS with the same zone test as the preflight and warn). Needs the new simulator `place` action (see correction 1).
- **Take counter** and **Mark replay start / end**: record timestamps and call Deck's replay endpoint (`GET /api/replay`) to save each take to `recordings/launcher/takes/take-NNN.json` for the hosted replay.
- **Self-check** button: runs `scripts/sim_motion_check.py` (about 2 minutes, it resets the scene) and shows PASS/FAIL lines. Disable it while a take is recording.
- **Open**: Deck, the workspace page, the logs folder.
- Show live: hand-gate state and fps, SAM health, voxel source ages, last Spark call cost and total (from Deck's timeline).

### 5. Glasses link (optional, collapsed by default)
- Wi-Fi IPv4 and the relay address `https://<ip>:8772`.
- One button to create the TLS certificate (`scripts/create_glasses_tls.py <ip>`; it refuses to overwrite, so show "already exists for <ip>" and flag a mismatch if the laptop's IP changed) and one to start and stop the relay: `python -m lookandpick.glasses_gateway --bind <ip> --public-host <ip> --port 8772 --deck-url http://127.0.0.1:8765 --cert recordings/glasses-gateway/tls/server.crt --key recordings/glasses-gateway/tls/server.key`. Show the path of `ca.crt` to install on the phone and the PATH (never the contents) of the pairing token file. The iPhone app has never been compiled, so also show the file route (a glasses photo plus a voice clip) as the dependable alternative, linking `docs/demo-2026-11-04.md`.

### Stop all
One button: cancels any reconstruction first (correction 2), then stops relay, Deck, SAM worker, simulator (in that order), waits for the processes, confirms the GPU lock is free, and reports what is still running. Services it started are stopped by process group; services it did not start get a signal to that one PID, after asking (see the smaller points).

## Exact start commands (verified today)

Run from the repo root. Use the interpreters shown: they have the right packages.

```bash
# simulator bridge (http://127.0.0.1:8766, health: GET /api/state)
simulation/.venv/bin/python -u simulation/run.py

# SAM worker (http://127.0.0.1:8767, health: GET /health; holds the shared GPU lease for its whole life; ~15 s to load)
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ~/src/sam3-venv/bin/python scripts/sam_worker.py

# Deck for SETUP (no cameras, no SAM)
PYTHONPATH=. workspace_splat/.venv/bin/python -u -m lookandpick.mission_control --backend sim --model <fake|spark> --port 8765

# Deck LIVE (both cameras, hand gate, SAM people). Needs the hands environment (mediapipe, cv2).
PYTHONPATH=. ~/src/hands-venv/bin/python -u -m lookandpick.mission_control --backend sim --model <fake|spark> --port 8765 \
  --hand-gate <ace pro index> --intrinsics calibration/ace-pro.json \
  --second-camera <ugreen index> --second-intrinsics calibration/ugreen.json --sam-people
```
Check that the workspace pages still work when Deck runs under `hands-venv` (it has no scipy; the voxel API worked in today's live run, the reconstruction runs as its own subprocess). If a page needs the workspace environment, say so in `docs/launcher.md` and have the launcher choose the interpreter per stage.

## Gotchas learned today (do not rediscover them)

- The SAM worker holds `recordings/.gpu.lock` until it exits, so the reconstruction and the tests HANG while it runs. Stop it before the Setup stage.
- A previous Deck still holds the cameras: "Cannot open camera camN" or "marker seen in 0 frames" right after a restart means the old process is not gone yet. Stop and wait for exit before starting a new one.
- Camera calibration needs the marker in view and hands OUT of view for about 10 s.
- A hand stop latches the simulator ("stop latched; reset required"). The Deck reset button (or saying "carry on" for a person's stop) clears it; the launcher's Reset scene must call the reset.
- Terminal quirks: kill by process group, never by a name pattern.
- Spark and Voice need the internet; the hand gate, the voxel map and the stop never do.
- Spark needs `max_tokens` of about 2500 when it also labels a scene (already handled in code).

## Safety rules for the launcher (non-negotiable)

- It NEVER starts, imports or touches the real arm: no `scripts/so101_*`, no `lookandpick/adapters/so101*.py`, nothing on `/dev/ttyACM*`, and it never calls Deck's `/api/real-arm/connect` or `/disconnect`. The real arm is out of scope and stays unpowered; the page says so with the manual tick-box above.
- Loopback only, no remote control, no endpoint that takes a command line or a path from the request.
- It does not change any safety logic. Do NOT edit `lookandpick/tools.py`, `safety.py`, `runtime.py`, `hand_safety.py`, `hand_gate.py`, `recovery.py`, `zones.py`, `models/*`, `adapters/*`. Ask Claude for anything you need from them. The other agent adding Muse Spark Open is editing `models/meta.py`, `glasses.py` and the `--model` flags.

## Tests (no hardware, no GPU, no network)

`tests/test_launcher.py`, run with `workspace_splat/.venv/bin/python -m pytest`: use tiny fake services (a script that serves `/health`, one that exits immediately, one that ignores SIGTERM) to prove: the stage order and the GPU handover (SAM only starts after the reconstruction stand-in has released the lock; stopping SAM frees it); health gates time out with a plain message; a port conflict is reported with the process; `Stop all` leaves no child processes (check the process group); no secret ever appears in a response or log (put a fake key in the environment and grep); non-loopback `Host` is refused; the safety tick-boxes block the later stages; the zone warning fires when a preset object is inside a no-go cube (real grid + `ManualZoneGuard`). Camera and GPU probes sit behind small functions you can fake.

## Order of work (stop after any item and it still works)

1. Hardware check + Stop all + the page shell (**P0**).
2. Setup stage and Go live with the health gates, opening Deck (**P0**).
3. Reset scene with presets, the zone warning, take counter (**P1**).
4. Self-check, live status strip, Mark replay start/end (**P1**).
5. Glasses link stage (**P2**).
6. Marker thumbnails and polish (**P3**).

## Acceptance (Liam present, at the machine)

- With both cameras plugged in: Hardware check all green; unplug one: red with the fix; re-plug: green after Re-check.
- Click through Setup to Go live without typing; Deck opens; the gate shows both cameras; stepping into view turns voxel cubes red.
- Reset scene between two takes leaves the arm at Rest, no latch, objects clear of the cubes.
- Stop all leaves nothing running and the GPU lease free (`nvidia-smi` shows the memory back).
- `PROGRESS.md` says what was tested for real and what was only tested with fakes.

## Time

Liam wants this usable for filming tonight. Aim for P0 within about an hour so it can be tried live, then iterate. Simulation and hardware checks only; nothing here may power or move the real arm.
