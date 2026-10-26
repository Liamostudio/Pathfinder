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

- **Python env:** `python3.14-venv` isn't installed, so no virtualenv, no pytest, no OpenCV/numpy. Needs `sudo apt install python3.14-venv`, or tell me to use another route (e.g. uv).
- **GPU:** BIOS is already on Switchable Graphics, yet the RTX 5070 is still absent from `lspci` (Intel iGPU only), no NVIDIA driver, no `nvidia-smi`, `ubuntu-drivers devices` lists nothing. Secure Boot is **enabled**, so once the card appears the driver (`-open` variant) will need a MOK key enrolment or Secure Boot off. Next: try Discrete Graphics in BIOS and check BIOS More Settings for an iGPU-only toggle; then Lenovo Vantage/BIOS update. Blocks SAM 3.1 and cuRobo.
- **SAM 3.1 weights:** gated on Hugging Face (`facebook/sam3.1`); Liam needs to accept Meta's terms and authenticate.
- **GitHub:** no remote yet. Repo must be public; need the repo name/URL to push.
- **Credits:** confirm the Meta API credit amount on the event details page (transcript was garbled: ~$150 stated).
- **Unconfirmed:** Muse Voice Transcribe is reportedly streaming-only; Glimmer weights, size and MacBook serving route unchecked.
- **Cameras:** only the laptop's built-in camera is present (`/dev/video0`; `video1` is its metadata node). The two external webcams aren't plugged in or mounted yet.
- **No sudo for me:** needs a password, so system installs (`python3.14-venv`, NVIDIA driver) must be run by Liam.

## Log

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
