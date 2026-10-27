# Progress

**Milestone:** Prep (in progress) · **State:** On track · **Last updated:** 2026-10-26 22:30 UK

Hackathon window: 26 Oct – 4 Nov 2026 (submit by 9pm ET Wed 4 Nov = 2am UK Thu 5 Nov). Features frozen 3 Nov.

## Milestones

A box is ticked only when its acceptance test has passed, with how it was tested.

- [ ] **Prep:** webcams mounted, gauge part fits a servo, follower parts printing, cuRobo docs read
  - Status: not started on the physical items; cuRobo docs not read yet.
- [ ] **Cameras + calibration:** both cameras report the ArUco marker in the same place to within 1cm; a tapped pixel lands on the right table spot
  - Status: capture/record/replay code written, untested (no OpenCV on this machine yet). No calibration code.
- [ ] **Safe zones, sim arm:** cuRobo runs on the Legion; a hand crossing the sim arm's path pauses it within a frame; sim arm plans around a mug; visualiser shows both
  - Status: speed-and-separation logic and stop latch exist with unit tests, against a fake arm only. No cuRobo, no visualiser, no real hand detection.
- [ ] **Language to target:** "pick up the apple" highlights the right apple and gives a 3D goal; "the left one" picks correctly between two; new objects labelled within a couple of seconds
  - Status: not started. Tool surface (`get_scene/pick/place/stop/say`) exists but no model is wired in.
- [ ] **Real arm swap-in:** calibrated in LeRobot, offset measured, real arm follows the twin's plan and picks a light prop
  - Status: not started. Servos due 27–30 Oct. Needs Liam present with the 6V kill switch in reach.
- [ ] **Hand pause + polish:** hand entering the path stops the arm 10/10 times, and Muse Spark says why
  - Status: not started.
- [ ] **Video + submit:** features frozen 3 Nov, submitted evening of 4 Nov
  - Status: not started.

## Working on now

- Repo scaffold and local-only safety layer (done in sim; hardware untouched).
- Setting up the Python environment so camera code and tests can actually run.
- Next: mission control skeleton with the model-call timeline; ArUco calibration with synthetic images.

## Blockers and decisions needed from Liam

- ~~**Python env**~~ **Resolved 2026-10-27:** `python3.14-venv` installed; `.venv` (git-ignored) has pytest, OpenCV, numpy, PyTorch.
- ~~**GPU**~~ **Resolved 2026-10-27:** Legion is in dGPU mode; `nvidia-driver-595-open` (595.99.02) installed, MOK key enrolled (Secure Boot stays on), `nvidia-smi` shows the RTX 5070 with 8151 MiB, CUDA 13.2. PyTorch 2.14.1+cu130 sees the GPU (see log).
- ~~**SAM 3.1 weights**~~ **Downloaded 2026-10-27:** access granted; `facebook/sam3.1` (3.5 GB `sam3.1_multiplex.pt` + configs) is in `~/.cache/huggingface`, outside the repo. Weights are open to download, not API-only. Not yet loaded or run; no Transformers integration, so needs Meta's SAM 3 GitHub repo. 8 GB VRAM fit untested.
- **SAM 3.1 on the 5070 (2026-10-27, works but slow):** `facebookresearch/sam3` cloned to `~/src/sam3`, installed `--no-deps` into scratch venv `~/src/sam3-venv` (Python 3.14, numpy 2.5, plus `pycocotools`). Cause of the earlier OOM: the predictor batches 16 frames through the detector; setting `model.batched_grounding_batch_size = 1` and `postprocess_batch_size = 1` fixes it. Tested with `scripts/sam3_smoke.py`: text-prompted tracking on Meta's `bedroom.mp4` runs, peak VRAM 4.47 GB, steady ~2.7 fps (first frame 5 s), no compile, no FA3, bf16 only on vision trunk + text encoder. **Correction (later 2026-10-27):** the earlier "0 objects" was my script reading the wrong output key (masks sit under `outputs`), not a model failure. With `scripts/sam3_live.py` the prompt "bed" on `bedroom.mp4` segments the whole bed on all 30 frames, checked by eye on frame 20; all 1623 checkpoint tensors verified loaded; 2.8 fps, peak 4.2 GB. Liam ran `sam3_live.py` on the webcam: window opens, masks drawn, reports it "seems like it's working" (not scored for accuracy, speed or hand detection). **Caveats:** 2.7 fps is far below the 15-30 Hz fast loop, so SAM 3.1 is a medium/slow-loop model and hand safety needs the lightweight detector. Upstream quirks worked around in the script: `offload_state_to_cpu` rejected by 3.1 `init_state`; `multiplex_count` must stay 16.
- **GitHub:** no remote yet. Repo must be public; need the repo name/URL to push.
- **Credits:** confirm the Meta API credit amount on the event details page (transcript was garbled: ~$150 stated).
- **Unconfirmed:** Muse Voice Transcribe is reportedly streaming-only; Glimmer weights, size and MacBook serving route unchecked.
- **Cameras:** only the laptop's built-in camera is present (`/dev/video0`; `video1` is its metadata node). The two external webcams aren't plugged in or mounted yet.
- **No sudo for me:** needs a password, so system installs (`python3.14-venv`, NVIDIA driver) must be run by Liam.

## Log

- **2026-10-27 (morning, UK):** NVIDIA driver installed by Liam via `apt install nvidia-driver-595-open` (open variant for the 5070). DKMS built and signed modules for both kernels; MOK key enrolled at boot. Tested: after reboot `nvidia-smi` lists the RTX 5070 (8151 MiB, driver 595.99.02, CUDA 13.2), 4 nvidia modules loaded, `mokutil --test-key` says enrolled. Then created `.venv` with `pip install -e ".[dev,vision]"` plus `torch` (cu130). Tested: pytest 5/5 pass; `torch.cuda.is_available()` True on RTX 5070 Laptop (sm_120), 2048x2048 matmul on GPU ran. Not done: `torch` isn't in `pyproject.toml` yet. No code change.
- **2026-10-26 (22:15, UK):** Diagnosed blockers (no code change): dGPU missing from PCI bus; only the built-in webcam present; no passwordless sudo.
- **2026-10-26 (evening, UK):**
  - Read kickoff transcript: building opens 26 Oct, not 27 Oct as the earlier notes said.
  - Scaffolded repo: `README.md`, `pyproject.toml`, `.gitignore`, `.env.example`, `lookandpick/{world,safety,tools,demo}.py`, `lookandpick/adapters/{base,fake}.py`.
  - Added `lookandpick/adapters/camera.py`, `lookandpick/record.py`, `docs/model-access.md` (model access notes from web search, secondary sources only).
  - Tested: 5 safety tests pass (run by hand, pytest not installed); sim demo runs. Camera code only syntax-checked, never run.
  - Broke / not done: venv creation failed (no `python3-venv`); no git identity was set (repo-local identity set to Liam).

## Built during the hackathon

Written during the event unless marked otherwise.

| Component | Written | Notes |
| --- | --- | --- |
| World state (`world.py`) | 2026-10-26, `7a1353e` | Shared state; thread-safe snapshot |
| Safety layer (`safety.py`) | 2026-10-26, `7a1353e` | Speed-and-separation, stop latch; distances are placeholders |
| Tool surface (`tools.py`) | 2026-10-26, `7a1353e` | Safety-gated `get_scene/pick/place/stop/say` |
| Adapter interfaces + fakes (`adapters/base.py`, `fake.py`) | 2026-10-26, `7a1353e` | Fake tracker, sim arm |
| Sim demo (`demo.py`), tests (`tests/test_safety.py`) | 2026-10-26, `7a1353e` | |
| Camera sources + recorder (`adapters/camera.py`, `record.py`) | 2026-10-26, `de4dcc1` | Untested |
| Model access notes (`docs/model-access.md`) | 2026-10-26, `de4dcc1` | Secondary sources |

**Used, not built:** SAM 3.1 (Meta), cuRobo (NVlabs), LeRobot (Hugging Face), SO-101 URDF (community). None integrated yet.
