# Progress

**Milestone:** Prep (in progress) · **State:** On track · **Last updated:** 2026-10-28 UK · **Repo:** https://github.com/Liamostudio/Pathfinder (public)

Hackathon window: 26 Oct – 4 Nov 2026 (submit by 9pm ET Wed 4 Nov = 2am UK Thu 5 Nov). Features frozen 3 Nov.

## Milestones

A box is ticked only when its acceptance test has passed, with how it was tested.

- [ ] **Prep:** webcams mounted, gauge part fits a servo, follower parts printing, cuRobo docs read
  - Status: laptop GPU, driver, Python env and SAM 3.1 are ready (see log). Printed (Liam, 28 Oct): all SO-101 follower arm parts, and the ArUco marker panel (3D-printed, 160 mm plate, 120 mm marker). Not done: servos not arrived, webcams not mounted, gauge-part fit unchecked, cuRobo docs not read.
- [ ] **Cameras + calibration:** both cameras report the ArUco marker in the same place to within 1cm; a tapped pixel lands on the right table spot
  - Status: calibration code written and tested on synthetic views only (`lookandpick/calibration.py`, 7 tests pass): marker pose, pixel to table position, optional object height. Synthetic error 1–5 mm against the 1 cm target; two cameras 90° apart agree within 3 mm. **Not tested on real cameras or a printed marker**, no multi-camera fusion, no hand-tap UI. MuJoCo sim renders (`simulation/`) with the real marker texture are recovered within 1 cm at 1280x720, a stricter check than the synthetic ones. Recording/replay now tested with synthetic MP4s by Codex; camera failures and recording cleanup covered. Physical webcam adapter/record CLI still untested. Two cameras present (built-in plus Insta360 Ace Pro). Print-ready marker (`scripts/make_marker.py` -> `markers/aruco_id0_120mm.pdf`, A4 vector, rasterised back and measured at 119.7 mm of 120) and a real-camera check (`scripts/check_calibration.py`: pose, 10 cm grid, click-to-read table cm) written; the check recovers the camera position exactly on a synthetic render but has **not been run on a real camera or a printed marker**. Lens calibration (`scripts/calibrate_lens.py`) is written and tested on synthetic corner views only; until it has been run on a real board, intrinsics are a guessed field of view.
- [ ] **Safe zones, sim arm:** cuRobo runs on the Legion; a hand crossing the sim arm's path pauses it within a frame; sim arm plans around a mug; visualiser shows both
  - Status: MuJoCo SO-101 sim (`simulation/`, browser 3D view) runs the real `Tools`/`Safety` with scenario runs (100/100 on seeds 0-9, simulated-time latencies only; cubes only, kinematic servos).  MediaPipe hand adapter, calibrated landmark envelopes and a separate 100 Hz watchdog implemented and tested with SimArm (150 ms development timeout; fails closed on startup/stalls/errors/end-of-source). CPU recorded static-hand test: 60/60 frames detect, mean 21.4 ms/p95 28.4 ms inference; real detector + synthetic table calibration ran 30 paced frames. Full suite 99 pass. GUI still uses synthetic hand scenarios; no live hand feed, cuRobo, physical arm twin or physical acceptance.
- [ ] **Language to target:** "pick up the apple" highlights the right apple and gives a 3D goal; "the left one" picks correctly between two; new objects labelled within a couple of seconds
  - Status: partly de-risked, not done. SAM 3.1 turns a text prompt into a mask and tracks it (bed on Meta's sample clip, Liam saw it working on the webcam), but there's no 3D goal, no "the left one", no Muse Spark, and it isn't wired into the tool surface.
- [ ] **Real arm swap-in:** calibrated in LeRobot, offset measured, real arm follows the twin's plan and picks a light prop
  - Status: SO-101 adapter scaffold now has bounded dry-run commands, presence/commissioning gates, lazy LeRobot wrapper and a read-only health helper. Fake-bus tests only; no supplied physical FK/IK or commissioned profile, LeRobot is installed in a scratch venv (imports checked only) and there is no serial connection. Servos delayed: not arrived as of 28 Oct, hoping for delivery today; hardware cut-off is still 30 Oct. If they miss it, the core build continues on the MuJoCo sim arm and the real arm follows for the video. Power supply and barrel-jack to screw-terminal adapters have arrived: supply is **5V 10A** (5.5 x 2.1 mm, centre-positive), not 6V, so project docs now say 5V throughout. Liam must read ~5V at the adapter terminals with a multimeter before connecting the URT board, and must be present with the 5V kill switch in reach.
- [ ] **Hand pause + polish:** hand entering the path stops the arm 10/10 times, and Muse Spark says why
  - Status: lightweight MediaPipe detector and continuous watchdog code are in place (see safe-zones status and `docs/hand-safety.md`). Moving/occluded hand accuracy, live-camera loop, physical stopping latency and 10/10 hand-stop acceptance remain untested; Muse Spark narration is not wired.
- [ ] **Video + submit:** features frozen 3 Nov, submitted evening of 4 Nov
  - Status: not started.

## Working on now

- Machine and model setup is done: driver, Python env, SAM 3.1 on the 5070, LeRobot in a scratch venv. Arm servos delayed (hoped for 28 Oct, cut-off 30 Oct); build starts when they arrive, otherwise sim arm first.
- Deck mission control is built; the next connection is timestamped hand observations/model events. The hand watchdog and SO-101 dry-run work are documented in `docs/hand-safety.md` and `docs/so101-bringup.md`.
- Next: use the printed marker panel with `scripts/check_calibration.py` (marker is printed; 120 mm); `sudo usermod -aG dialout $USER` (Liam), then print the marker and checkerboard (`markers/`, 100% scale), run `scripts/calibrate_lens.py --cam 2 --name ace-pro`, then `scripts/check_calibration.py --intrinsics calibration/ace-pro.json`; test the selected MediaPipe detector on representative recorded moving hands and then the calibrated live-camera feed.
- Open question: try `torch.compile` on SAM 3.1 (Meta claims ~2x), and test it on hands and people, not just the bed clip.

## Blockers and decisions needed from Liam

- **Credits:** confirm the Meta API credit amount on the event details page (transcript was garbled: ~$150 stated).
- **Cameras:** built-in camera is `/dev/video0` (`video1` is metadata). An Insta360 Ace Pro (USB webcam mode) is now on `/dev/video2` (`video3`–`5` are other nodes): captures 1920x1080, 1280x720 and 640x360 at 30 fps, tested with one frame each. It is a very wide, strongly distorted lens, so it needs a checkerboard lens calibration before the marker check means anything (the 70° default is wrong). Lens calibration code is written (`scripts/calibrate_lens.py`, `lookandpick/intrinsics.py`, board PDF `markers/checkerboard_6x9_20mm.pdf`). Not yet mounted; the second external webcam is not plugged in.
- **Unconfirmed:** Muse Voice Transcribe is reportedly streaming-only; Glimmer weights, size and MacBook serving route unchecked.
- **Meta AI glasses (Adventurer style) received:** are they back in scope as an input (Wearables Device Access Toolkit via phone), or B-roll only? Check the return window against 9 Nov.
- **Servos and power:** servos not arrived; cut-off 30 Oct (fallback: sim arm for the core build, real arm for the video). Supply is 5V 10A; all project docs, the adapter's `5V KILL SWITCH READY` confirmation and its test now say 5V (STS3215 7.4V ceiling unchanged; servos will be weaker than at rated voltage). Check ~5V at the adapter terminals with a multimeter before connecting the URT board.
- **Stray file:** a screenshot of the SAM preview window sits in the repo root. It is now git-ignored (`*_screenshot_*.png`), but it likely shows Liam's face, so delete or move it.
- **Serial permission:** Liam is not in `dialout`; run `sudo usermod -aG dialout $USER` and log out/in before the URT board will work.
- **No sudo for me:** needs a password, so system installs must be run by Liam.
- Resolved 2026-10-27: Python env (`python3.14-venv`), NVIDIA driver, Hugging Face access to `facebook/sam3.1`, GitHub (`gh` login, public repo `Pathfinder`, pushed).

## SAM 3.1 setup notes (2026-10-27)

- Weights: `facebook/sam3.1` (3.5 GB `sam3.1_multiplex.pt`) in `~/.cache/huggingface`, outside the repo. Open to download, not API-only; all 1623 checkpoint tensors verified loaded.
- Code: `facebookresearch/sam3` cloned to `~/src/sam3`, installed `--no-deps` into scratch venv `~/src/sam3-venv` (Python 3.14, numpy 2.5, extras `pycocotools` and full `opencv-python`). Meta pins `numpy<2` and Python 3.12+; both ignored and it still runs.
- Memory fix: the predictor batches 16 frames through the detector and ran out of memory on 8 GB. Setting `model.batched_grounding_batch_size = 1` and `postprocess_batch_size = 1` fixes it. Also bf16 on the vision trunk and text encoder, and `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- Upstream quirks worked around in the scripts only: `offload_state_to_cpu` rejected by 3.1 `init_state`; `multiplex_count` must stay 16; FlashAttention 3 not installed.
- Measured: peak VRAM ~4.2–4.5 GB, ~2.8 fps steady (first frame ~5 s), no compile, no FA3. Detection checked by eye on one frame (bed, `bedroom.mp4`); not scored for accuracy, and hands and people untested.
- Correction: an earlier "0 objects found" came from my script reading the wrong output key (masks sit under `outputs`), not from the model.

## LeRobot setup notes (2026-10-27)

- Installed `lerobot` 0.6.1 `--no-deps` into scratch venv `~/src/lerobot-venv` (outside the repo) with `feetech-servo-sdk`, `pyserial`, `deepdiff`, `draccus`, CPU `torch`, numpy 2.5. Its pins (Python 3.12+, `numpy<2.3`, `torch<2.12`) are ignored, as with SAM. Tested: `SO101Follower`, `SO101FollowerConfig` and `FeetechMotorsBus` import; the `lerobot-*` CLIs (`find-port`, `setup-motors`, `calibrate`, `teleoperate`) are installed but **not run: no URT board or servos connected yet**.
- **Needs Liam (sudo):** `sudo usermod -aG dialout $USER`, then log out and back in, or the serial port will be permission denied. No `/dev/ttyUSB*` exists until the URT board is plugged in.

## Log

- **2026-10-28 (UK), hardware (from Liam):** servos delayed, hoped for today, cut-off still 30 Oct (fallback above). Printed: all follower arm parts and the ArUco marker panel (160 mm plate, 120 mm marker), so the print-the-marker step is done. Arrived: power supply and barrel-jack to screw-terminal adapters (5V 10A, 5.5 x 2.1 mm, centre-positive; check ~5V with a multimeter before connecting the URT board). Meta AI glasses received (scope question in Blockers). No acceptance test has changed; no milestones ticked.
- **2026-10-28 (UK):** Renamed `Simulation Environment/` to `simulation/` (no space in the path), repaired its venv paths, restarted the server, moved the 5 MB scenario samples out of git (kept `reports/acceptance-summary.json`). Tested: 28 sim tests and 103 project tests pass; server answers HTTP 200. Pushed through `693c61e`.
- **2026-10-27 (UK):**
  - Lens calibration and tools: `lookandpick/intrinsics.py`, `scripts/calibrate_lens.py`, checkerboard PDF, `check_calibration.py --intrinsics`; Insta360 Ace Pro found on `/dev/video2` (1080p/720p/360p at 30 fps). LeRobot 0.6.1 installed `--no-deps` in `~/src/lerobot-venv` after Liam approved; imports verified. Wrote `docs/codex-brief-hand-and-arm.md` and `docs/sim-brief.md` for Codex. Tested: 4 intrinsics tests on synthetic views; nothing run on a real board or servo.
  - Fixed simulation preview lifetime after the chat-launched server stopped between turns: `simulation/start.py` now starts an independent session, logs to `server.log`, waits for HTTP health and reuses a healthy existing server. Verified: launcher returned successfully; separate invocation reused it; browser connected and rendered the scene.
  - Built `simulation/`: headless MuJoCo SO-101 (Pathfinder lime), browser 3D workbench, Arm/Tracker HTTP bridge, movable RGB/depth/segmentation cameras, marker-frame offset and real ArUco texture/distortion, seeded fault scenarios and offline trajectory replay. Tested: 28 simulator tests pass; unchanged Tools pick/place over HTTP; rendered two-camera calibration and distorted-render pose recovery within 1 cm at 1280×720; 10 scenarios × 10 seeds = 100/100 pass; existing suite 98 pass at check time. Python 3.14 wheel/EGL verified. Full comparison and remaining gaps in `simulation/BRIEF-CHECK.md`: kinematic servos/ideal grasps, two cubes only, incomplete fault matrix and no direct Deck backend integration. No hardware/model accuracy claims or physical milestone ticks.
  - Codex hand/arm brief: added HandDetector + MediaPipe CPU implementation, table projection/envelopes and optional frame transform, independent watchdog, monitored tracker and recorded-clip CLI. Added bounded SO-101 dry-run/LeRobot wrapper, read-only health helper and bench docs. Tested: 99 total tests (33 new); core-only import check 68 pass/14 skip; CPU static-hand replay mean 21.4 ms/p95 28.4 ms (60/60 detected), 30-frame paced monitored replay with synthetic calibration. MediaPipe 1.1.1 works on Python 3.14.4 in scratch venv. Initial push of main through `2e219c3` succeeded. No calibration/intrinsics/existing-script edits; no LeRobot installation, serial connection or real motion. Physical limits/force/stopping and live hand accuracy unverified.
  - Built Deck mission control (`lookandpick/mission_control.py`, `lookandpick/web/`): schematic scene, object selection, simulated hand gate, pick/place, stop/reset, guided demo, timeline/export, readiness view and browser-local media previews. Uses existing Python tool/safety classes; models and hardware not connected. Tested: 62 Python tests, JS syntax, wheel assets, browser guided demo/blocked pick/stop/reset/themes and 390 px layout. Browser WebM decode passed; mp4v rejected with handled error. Webcam not tested. Details: `docs/mission-control.md`.
  - GitHub set up by Liam (`gh auth login`); I created the public repo `Liamostudio/Pathfinder` and pushed `main` (9 commits, commit email `liam@liam-o.com` left public by Liam's choice). Checked no secrets, weights or recordings are tracked. Tested: `git ls-remote`/`gh repo view` show the repo public with `main`.
  - Wrote `lookandpick/calibration.py` and `tests/test_calibration.py` (ArUco marker pose, pixel to table position). Tested: 7 pass on synthetic renders from known camera poses; errors 1–5 mm. Not tested on a real camera.
  - Codex audit completed: controller/state, camera/recording, calibration input and SAM script fixes; 34 new regression cases. Full suite 46/46 passes; core-only import simulation 24 pass/9 skip; sim demo runs. Actual MP4 round trip tested with synthetic frames. SAM I/O/cleanup tested with a fake predictor; GPU rerun attempted but CUDA unavailable to this session. See `docs/audit-2026-10-27.md`; fixes remain local, not pushed by Codex at the time (all since pushed). README now lists SAM 3.1 under open source and the new scripts under built work.
  - NVIDIA driver `nvidia-driver-595-open` installed by Liam; DKMS modules signed; MOK key enrolled at boot. Tested: `nvidia-smi` shows the RTX 5070 (8151 MiB, CUDA 13.2), 4 nvidia modules loaded, `mokutil --test-key` enrolled.
  - Created `.venv` with `pip install -e ".[dev,vision]"` plus `torch` (cu130). Tested: pytest 5/5; `torch.cuda.is_available()` True (sm_120), matmul on GPU ran. `torch` not yet in `pyproject.toml`.
  - Hugging Face access granted by Liam; SAM 3.1 checkpoint downloaded after Liam approved the 3.5 GB size.
  - Got SAM 3.1 running on the 5070 (see setup notes); wrote `scripts/sam3_smoke.py` and `scripts/sam3_live.py` (record webcam clip, track a prompt, play back with masks). Tested: sample clip headless, and Liam ran the webcam window ("seems like it's working").
  - Not done: SAM last measured at ~2.8 fps; no accuracy, hand or person testing. Physical webcam adapter/record CLI untested. SAM scripts now accept explicit paths and clean up resources, but still need the scratch environment and a GPU rerun after audit changes.
- **2026-10-26 (22:15, UK):** Diagnosed blockers (no code change): dGPU missing from PCI bus; only the built-in webcam present; no passwordless sudo.
- **2026-10-26 (evening, UK):** Read kickoff transcript (building opened 26 Oct, not 27 Oct). Scaffolded repo, safety layer, tool surface, fake adapters, camera/record code and model access notes. 5 safety tests passed by hand; camera code only syntax-checked.

## Built during the hackathon

Written during the event unless marked otherwise.

| Component | Written | Notes |
| --- | --- | --- |
| World state (`world.py`) | 2026-10-26, `7a1353e` | Shared state; thread-safe snapshot |
| Safety layer (`safety.py`) | 2026-10-26, `7a1353e` | Speed-and-separation, stop latch; distances are placeholders |
| Tool surface (`tools.py`) | 2026-10-26, `7a1353e` | Safety-gated `get_scene/pick/place/stop/say` |
| Adapter interfaces + fakes (`adapters/base.py`, `fake.py`) | 2026-10-26, `7a1353e` | Fake tracker, sim arm |
| Sim demo (`demo.py`), tests (`tests/test_safety.py`) | 2026-10-26, `7a1353e` | 5/5 pass under pytest |
| Camera sources + recorder (`adapters/camera.py`, `record.py`) | 2026-10-26, `de4dcc1`; audited 27 Oct | Synthetic MP4 round trip and capture/writer failure tests pass; physical capture untested |
| ArUco calibration (`calibration.py`, `tests/test_calibration.py`) | 2026-10-27 | Synthetic images and invalid-input tests only; no physical acceptance |
| Marker generator + calibration check (`scripts/make_marker.py`, `check_calibration.py`, `tests/test_make_marker.py`) | 2026-10-27 | Synthetic and PDF round-trip only; no real camera |
| MuJoCo simulation workbench (`simulation/`) | 2026-10-27 | 28 contract/render/bridge tests; 100 seeded scenario runs pass; kinematic servo and ideal-grasp limits documented |
| Deck mission control (`mission_control.py`, `web/`, `tests/test_mission_control.py`) | 2026-10-27 | 14 new tests; local browser checks; synthetic state only |
| Lens calibration (`intrinsics.py`, `scripts/calibrate_lens.py`, `tests/test_intrinsics.py`) | 2026-10-27 | Recovers a known distorted camera from synthetic corner views (4 tests); live capture loop never run on a real board |
| Hand detector + watchdog (`adapters/hands.py`, `hand_safety.py`, `hand_loop.py`) | 2026-10-27 | Fake/recorded tests and CPU MediaPipe static-hand benchmark; no live-camera acceptance |
| SO-101 dry-run + read-only health (`adapters/so101*.py`, `scripts/so101_check.py`) | 2026-10-27 | Fake buses only; no hardware commissioning |
| Model access notes (`docs/model-access.md`) | 2026-10-26, `de4dcc1` | Secondary sources |
| SAM 3.1 smoke test + webcam preview (`scripts/sam3_smoke.py`, `sam3_live.py`) | 2026-10-27, `3bdd0a1` | Run from scratch venv; wraps Meta's code with memory workarounds |

**Used, not built:** SAM 3.1 (Meta, running locally via its repo), cuRobo (NVlabs), LeRobot (Hugging Face), SO-101 URDF (community). Only SAM is integrated so far, and only as a standalone script.
