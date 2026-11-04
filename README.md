# Look-and-Pick

## Standalone Gaussian splatting app

Open **Splat Studio** from your application menu, or run `workspace_splat/.venv/bin/python gaussian_studio/start.py`. It opens its own desktop window with image/folder/video ingestion, local Gaussian reconstruction, four quality presets including Ultra-high, duration estimates, orbit/pan/zoom, native export dialogs, and API-key settings for fal.ai or a custom provider adapter. It runs independently of the robot and simulation UI. See [the Splat Studio guide](gaussian_studio/README.md).

A table-mounted SO-101 robot arm that works safely around people. Meta's models see, understand and decide; a simple, checkable safety layer enforces. Models never drive the motors directly.

Entry for the Meta Global AI Developer Hackathon (indie track, "Push the Frontier").

## Built during the hackathon

Project-authored work started on **26 October 2026**, straight after the kickoff call. The first commit is the start point. Upstream libraries, models and robot assets predate the event and are credited separately below.

- World state, safety layer (speed-and-separation, stop latch) and the orchestrator tool surface
- Adapter interfaces with fake implementations for development against recorded footage
- Scripts to run SAM 3.1 on the 8 GB laptop GPU: a memory and speed test, and a webcam preview that tracks a text prompt (`scripts/`)
- ArUco table calibration with synthetic-image tests; real-camera validation still pending
- Deck mission control GUI: simulated scene, safety scenarios, pick/place controls, preview media and session timeline
- Lightweight hand detector, calibrated hand envelopes and an independent timeout watchdog (simulation tested)
- SO-101 adapter scaffold with bounded dry-run commands and a read-only health-check helper
- Photo/video workspace setup, persistent reconstruction jobs, editable planes, Gaussian exports and a separate lime URDF robot
- ArUco-anchored voxel volumes, faint animated cubes, row/single-cell editing and configurable grid fidelity
- Splat Studio developer tool and local SAM recorded-clip prompt UI
- Meta Adventurer/iPhone companion source, authenticated HTTPS relay and reviewed voice/photo jobs into the simulator; [setup and native acceptance still needed](mobile/PathfinderGlasses/README.md)
- Grasp orientation from fused SAM masks: size and long-axis yaw of an object from two or three calibrated cameras (`lookandpick/object_geometry.py`, synthetic-view tested; not yet on real cameras or wired into the live tracker; [plan](docs/grasp-orientation-plan.md))

## Open source we build on

| Component | Use | Licence / provenance |
| --- | --- | --- |
| [MapAnything](https://github.com/facebookresearch/map-anything) | Joint camera/depth reconstruction | Apache 2.0 source; **Apache 2.0 weights**, `facebook/map-anything-apache` revision `00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a`. Exact cached revision checked against the public Hugging Face model metadata on 3 Nov. The separate research checkpoint has different terms. |
| [gsplat](https://github.com/nerfstudio-project/gsplat) | Differentiable Gaussian rendering/densification | Apache 2.0; our orchestration, training loop and browser renderer were written here. |
| [COLMAP / PyCOLMAP](https://github.com/colmap/colmap) | Optional classical reconstruction | BSD 3-Clause. |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | CPU hand landmarks | Apache 2.0 code; downloaded Hand Landmarker model retained outside Git. |
| [EfficientDet-Lite0](https://www.kaggle.com/models/tensorflow/efficientdet) | Person-only detections | Apache 2.0 TensorFlow model; downloaded weights retained outside Git. |
| [cuRobo](https://github.com/NVlabs/curobo) | Planned GPU motion integration | Apache 2.0; not connected to Deck. |
| [LeRobot](https://github.com/huggingface/lerobot) | SO-101 terminal control | Apache 2.0; Deck never powers or moves hardware. |
| [SO-101 URDF/meshes](https://github.com/TheRobotStudio/SO-ARM100) | Independent lime robot and MuJoCo sim | Apache 2.0; upstream licence and pinned source retained in `simulation/assets/so101/`. |
| [SAM 3 / SAM 3.1](https://github.com/facebookresearch/sam3) | Segmentation/tracking | Meta **SAM License**, not Apache; gated `facebook/sam3.1` weights are not distributed here. |
| [Meta Wearables DAT](https://github.com/meta/meta-wearables-dat-ios) | iPhone glasses registration/device status | Meta Wearables Developer Terms; pinned SDK 1.0.1 dependency, not vendored. Native build/device acceptance pending. |
| [Depth Anything 3](https://github.com/ByteDance-Seed/Depth-Anything-3) | Optional developer-tool depth preview | Apache 2.0 source; checkpoint terms depend on the selected variant. Not the main workspace reconstruction path. |

Model weights, third-party source caches, original workspace photos, API keys and local calibration captures are excluded from these commits. See [dependency provenance](docs/dependency-provenance.md).

## Safety

- Safety checks run locally and never depend on the network.
- Slow as people approach, stop at minimum distance.
- "Stop" bypasses everything.
- The real arm is only ever powered with a person present and the 5V kill switch in reach.

The original fake workbench checks before simulated moves and gripper actions. The optional
MuJoCo Runtime backend has simulator separation checks and supports the independent live
hand watchdog; physical full-arm geometry and end-to-end bench acceptance remain pending. See the [code audit](docs/audit-2026-10-27.md) for remaining requirements.

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m lookandpick.demo
```

Keys go in `.env` (see `.env.example`); it is git-ignored.

Install `pip install -e ".[dev,vision]"` to run calibration and OpenCV recording/replay tests.
With only the `dev` extra, tests requiring vision dependencies skip automatically.

The SAM 3.1 scripts need Meta's code, installed separately outside this repo, and a Hugging Face login with access to the gated weights; see `PROGRESS.md` for the working setup and its workarounds.

## Mission control

```sh
.venv/bin/python -m lookandpick.mission_control
```

Open **http://127.0.0.1:8765** and click **play demo**. Uses the existing simulator and safety
gate; no GPU, API key or frontend build needed. Webcam and local recording previews are
available, with no detection applied. Robot actions use synthetic state; physical hardware is not connected.

For the MuJoCo Runtime and typed orchestrator commands, add `--backend sim --model fake` (or `--model spark` with your configured Meta key). Start `simulation/start.py` first. STOP remains available during a command. Deck also has a read-only arm panel, local CSV trace charts and a [static submission replay](replay/README.md).

Choose **set up workspace** for local photo/video reconstruction with MapAnything,
SAM 3.1 and gsplat, editable surfaces and a separate lime SO-101. Save and activate
visual workspaces without changing physical calibration. The CAD editor also supports
drawn and extruded voxel working areas with ArUco anchoring and local RGB people/hand
monitoring. See [local workspace setup](docs/local-workspaces.md) and [voxel working areas](docs/voxel-working-area.md).

See the [mission control guide](docs/mission-control.md) for controls, media formats and integration points.

See [hand safety](docs/hand-safety.md) for optional MediaPipe setup and recorded-clip checks,
and [SO-101 bring-up](docs/so101-bringup.md) for dry-run limits and Liam's bench checklist.

## SAM Prompt example

```sh
.venv/bin/python -m lookandpick.sam_prompt
```

Open **http://127.0.0.1:8767** in Chrome or Edge. Connect the webcam, allow camera
access, select the external camera (e.g. Insta360), and record 3–10 seconds. Enter
an object prompt and click **Identify in recorded clip**. SAM 3.1 processes the
recorded clip locally, then plays the masks. Change the prompt to reuse the clip.
**Export result** downloads a browser-compatible WebM; **Save original** downloads
the untouched recording. Original WebM, annotated MP4, JPEG frames and inference
logs are retained under `recordings/sam-prompt/<clip-id>/` (git-ignored).
Presentation mode expands the result for filming. Recordings contain video only.

Requires the existing CUDA/SAM scratch environment at `~/src/sam3-venv` and cached
SAM 3.1 weights. Override with `--python /path/to/python`; choose another port with
`--port 8768`. Processing samples at 10 fps, up to 100 frames, at 640 px width; it
is recorded-clip segmentation, not live inference. Allow up to a minute for a
short clip, plus one playback duration to prepare the export. Keep the tab visible
while processing/exporting. Run from the project checkout (the inference script
is in `scripts/`). Clips stay on this machine; no external service is used.
