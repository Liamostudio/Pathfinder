# Progress

**Milestone:** Prep (in progress) · **State:** On track · **Last updated:** 2026-10-27 UK · **Repo:** https://github.com/Liamostudio/Pathfinder (public)

Hackathon window: 26 Oct – 4 Nov 2026 (submit by 9pm ET Wed 4 Nov = 2am UK Thu 5 Nov). Features frozen 3 Nov.

## Milestones

A box is ticked only when its acceptance test has passed, with how it was tested.

- [ ] **Prep:** webcams mounted, gauge part fits a servo, follower parts printing, cuRobo docs read
  - Status: laptop GPU, driver, Python env and SAM 3.1 are ready (see log). Physical items not started; cuRobo docs not read yet.
- [ ] **Cameras + calibration:** both cameras report the ArUco marker in the same place to within 1cm; a tapped pixel lands on the right table spot
  - Status: calibration code written and tested on synthetic views only (`lookandpick/calibration.py`, 7 tests pass): marker pose, pixel to table position, optional object height. Synthetic error 1–5 mm against the 1 cm target; two cameras 90° apart agree within 3 mm. **Not tested on real cameras or a printed marker**, no lens calibration, no multi-camera fusion, no hand-tap UI. Recording/replay now tested with synthetic MP4s by Codex; camera failures and recording cleanup covered. Physical webcam adapter/record CLI still untested. Only one camera present. Print-ready marker (`scripts/make_marker.py` -> `markers/aruco_id0_120mm.pdf`, A4 vector, rasterised back and measured at 119.7 mm of 120) and a real-camera check (`scripts/check_calibration.py`: pose, 10 cm grid, click-to-read table cm) written; the check recovers the camera position exactly on a synthetic render but has **not been run on a real camera or a printed marker**. Intrinsics are assumed from a guessed field of view until a lens calibration exists.
- [ ] **Safe zones, sim arm:** cuRobo runs on the Legion; a hand crossing the sim arm's path pauses it within a frame; sim arm plans around a mug; visualiser shows both
  - Status: speed-and-separation logic and stop latch exist against a fake arm only. Codex audit added finite-coordinate checks, gated gripper actions, adapter completion results, persistent grasp state and accurate partial outcomes. Full suite: 62 pass; continuous monitoring is not implemented. Deck GUI now visualises synthetic state and safety scenarios. No cuRobo, no physical arm twin, no real hand detection.
- [ ] **Language to target:** "pick up the apple" highlights the right apple and gives a 3D goal; "the left one" picks correctly between two; new objects labelled within a couple of seconds
  - Status: partly de-risked, not done. SAM 3.1 turns a text prompt into a mask and tracks it (bed on Meta's sample clip, Liam saw it working on the webcam), but there's no 3D goal, no "the left one", no Muse Spark, and it isn't wired into the tool surface.
- [ ] **Real arm swap-in:** calibrated in LeRobot, offset measured, real arm follows the twin's plan and picks a light prop
  - Status: not started. Servos due 27–30 Oct. Needs Liam present with the 6V kill switch in reach.
- [ ] **Hand pause + polish:** hand entering the path stops the arm 10/10 times, and Muse Spark says why
  - Status: not started. SAM 3.1 runs at ~2.8 fps, too slow for the fast loop, so a lightweight hand detector is still needed.
- [ ] **Video + submit:** features frozen 3 Nov, submitted evening of 4 Nov
  - Status: not started.

## Working on now

- Machine and model setup is done: driver, Python env, SAM 3.1 loading and tracking on the 5070.
- Next: mission control skeleton with the model-call timeline; print the marker (`markers/aruco_id0_120mm.pdf`, 100% scale) and run `scripts/check_calibration.py`; choose a lightweight hand detector for the fast loop.
- Open question: try `torch.compile` on SAM 3.1 (Meta claims ~2x), and test it on hands and people, not just the bed clip.

## Blockers and decisions needed from Liam

- **Credits:** confirm the Meta API credit amount on the event details page (transcript was garbled: ~$150 stated).
- **Cameras:** only the laptop's built-in camera is present (`/dev/video0`; `video1` is its metadata node). The two external webcams aren't plugged in or mounted yet.
- **Unconfirmed:** Muse Voice Transcribe is reportedly streaming-only; Glimmer weights, size and MacBook serving route unchecked.
- **Stray file:** a screenshot of the SAM preview window sits in the repo root. It is now git-ignored (`*_screenshot_*.png`), but it likely shows Liam's face, so delete or move it.
- **No sudo for me:** needs a password, so system installs must be run by Liam.
- Resolved 2026-10-27: Python env (`python3.14-venv`), NVIDIA driver, Hugging Face access to `facebook/sam3.1`, GitHub (`gh` login, public repo `Pathfinder`, pushed).

## SAM 3.1 setup notes (2026-10-27)

- Weights: `facebook/sam3.1` (3.5 GB `sam3.1_multiplex.pt`) in `~/.cache/huggingface`, outside the repo. Open to download, not API-only; all 1623 checkpoint tensors verified loaded.
- Code: `facebookresearch/sam3` cloned to `~/src/sam3`, installed `--no-deps` into scratch venv `~/src/sam3-venv` (Python 3.14, numpy 2.5, extras `pycocotools` and full `opencv-python`). Meta pins `numpy<2` and Python 3.12+; both ignored and it still runs.
- Memory fix: the predictor batches 16 frames through the detector and ran out of memory on 8 GB. Setting `model.batched_grounding_batch_size = 1` and `postprocess_batch_size = 1` fixes it. Also bf16 on the vision trunk and text encoder, and `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- Upstream quirks worked around in the scripts only: `offload_state_to_cpu` rejected by 3.1 `init_state`; `multiplex_count` must stay 16; FlashAttention 3 not installed.
- Measured: peak VRAM ~4.2–4.5 GB, ~2.8 fps steady (first frame ~5 s), no compile, no FA3. Detection checked by eye on one frame (bed, `bedroom.mp4`); not scored for accuracy, and hands and people untested.
- Correction: an earlier "0 objects found" came from my script reading the wrong output key (masks sit under `outputs`), not from the model.

## Log

- **2026-10-27 (UK):**
  - Built Deck mission control (`lookandpick/mission_control.py`, `lookandpick/web/`): schematic scene, object selection, simulated hand gate, pick/place, stop/reset, guided demo, timeline/export, readiness view and browser-local media previews. Uses existing Python tool/safety classes; models and hardware not connected. Tested: 62 Python tests, JS syntax, wheel assets, browser guided demo/blocked pick/stop/reset/themes and 390 px layout. Browser WebM decode passed; mp4v rejected with handled error. Webcam not tested. Details: `docs/mission-control.md`.
  - GitHub set up by Liam (`gh auth login`); I created the public repo `Liamostudio/Pathfinder` and pushed `main` (9 commits, commit email `liam@liam-o.com` left public by Liam's choice). Checked no secrets, weights or recordings are tracked. Tested: `git ls-remote`/`gh repo view` show the repo public with `main`.
  - Wrote `lookandpick/calibration.py` and `tests/test_calibration.py` (ArUco marker pose, pixel to table position). Tested: 7 pass on synthetic renders from known camera poses; errors 1–5 mm. Not tested on a real camera.
  - Codex audit completed: controller/state, camera/recording, calibration input and SAM script fixes; 34 new regression cases. Full suite 46/46 passes; core-only import simulation 24 pass/9 skip; sim demo runs. Actual MP4 round trip tested with synthetic frames. SAM I/O/cleanup tested with a fake predictor; GPU rerun attempted but CUDA unavailable to this session. See `docs/audit-2026-10-27.md`; fixes remain local, not pushed by Codex. README now lists SAM 3.1 under open source and the new scripts under built work.
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
| Deck mission control (`mission_control.py`, `web/`, `tests/test_mission_control.py`) | 2026-10-27 | 14 new tests; local browser checks; synthetic state only |
| Model access notes (`docs/model-access.md`) | 2026-10-26, `de4dcc1` | Secondary sources |
| SAM 3.1 smoke test + webcam preview (`scripts/sam3_smoke.py`, `sam3_live.py`) | 2026-10-27, `3bdd0a1` | Run from scratch venv; wraps Meta's code with memory workarounds |

**Used, not built:** SAM 3.1 (Meta, running locally via its repo), cuRobo (NVlabs), LeRobot (Hugging Face), SO-101 URDF (community). Only SAM is integrated so far, and only as a standalone script.
