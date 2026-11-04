# Pathfinder

## What Pathfinder is

Pathfinder is a safety layer that lets a robot arm work around people. Meta's models see, understand and decide; a small, checkable local layer enforces. Models never drive the motors.

Solo entry by Liam for the Meta Global AI Developer Hackathon, indie track, “Push the Frontier”. Evidence below refers to the dated log in [PROGRESS.md](PROGRESS.md); later entries supersede its older milestone summaries.

## What it does, as built

| Feature | State on 4 November 2026 | Evidence in PROGRESS.md / checks |
| --- | --- | --- |
| Hand gate | Local CPU MediaPipe Hand Landmarker; two calibrated cameras fused into one gate. Slows/stops the simulated arm; independent 150 ms watchdog and latched stops. Either camera stalling stops the arm. | 4 Nov “second camera running behind the fused hand gate, live”: 80 s deliberate hand test, about 32 fps; `tests/test_hand_gate.py` fusion/stall tests. Physical stopping latency and detection accuracy unmeasured. |
| SAM 3.1 people/hands → voxels | Local segmentation on both cameras, turning cubes red. About 0.6–1.0 s per look: **too slow to be the safety path**. Camera detections never certify a cube as free. | 4 Nov “live camera test…” and “second camera running…”: about 700 occupied cubes with a person present, back to 18 manual cubes after departure; `tests/test_sam_people.py`, `tests/test_voxel_feed.py`. No ground-truth accuracy score. |
| Manual no-go cubes | User-drawn restrictions are hard rules for simulated moves. Bounded routing tries over/around; blocked targets or no tested route mean refusal and Rest. | 4 Nov “no-go cubes enforced…”, “pure route planner”, “motion tasks 1 to 5”: real-grid checks and 10/10 MuJoCo bridge behaviours (`scripts/sim_motion_check.py`). Fixed obstacles are marked by the user, not detected. |
| Glasses voice and five constrained tools | Glasses microphone → iPhone app → Muse Voice Transcribe → reviewed transcript → Muse Spark → validated `get_scene`, `pick`, `place`, `stop`, `say`. Liam reports replies played in the glasses. | 4 Nov evening “SPOKEN commands…”: real speech/APIs, simulated pick/place, missed-grip retry and local STOP. Real imported-photo “this one”, noisy-room and disconnect tests unverified. |
| Motion behaviours | Waits for hands; retries missed grips and re-aims moved targets. Bounded fault recovery; person STOP needs explicit Carry on/reset. Cancel attempts safe put-back then Rest. | 4 Nov “motion tasks 1 to 5”: about 40 new tests repeated three times, 10/10 MuJoCo bridge checks; “simulator Rest…”: 98 passed, 5 skipped. New persistence prompt not separately accepted on Spark; a grip retry was seen in the evening glasses run. |
| Workspace scan and voxel editor | Imported photos/video → local MapAnything geometry + SAM 3.1 masks + gsplat appearance; editable planes, marker anchoring, saved versions and manual voxel restrictions. | 2 Nov “main-app local workspace setup” and “voxel working-area editor”: real four-photo GUI reconstruction, save/reopen/tests. 4 Nov evening “re-scan and robot placement”: phone-photo scan reconstructed after reducing views following GPU out-of-memory. Glasses video decoded; reconstruction not run. |

## What is simulated and what is real

**The moving arm in the demo is a MuJoCo simulation of the SO-101.** Default object positions come from simulation ground truth. A separate Spark/SAM vision path has been tested on simulator images (3 Nov “language to target”), not accepted on real objects. Simulator servos are kinematic and grasps idealised.

The real SO-101 is built and calibrated. Tip-touch calibration gave about **2 mm height error on held-out touches** (3 Nov touch-calibration entry); this is not general 3D accuracy or a stopping-distance measurement. Powered tests so far are joint-level only. No live Cartesian real-arm move, pick/place or real-arm hand-stop through this pipeline is recorded. The safety layer is validated on the simulator, **not certified**. See the 30 Oct–3 Nov hardware entries and 4 Nov simulator-only motion/glasses entries in [PROGRESS.md](PROGRESS.md).

The cameras, live hand/person observations and evening glasses speech/API calls are real. Fixed obstacles are marked by the user. The iPhone companion is foreground tap-to-speak with transcript review; no wake word or glasses camera streaming is claimed. Any hosted recording must be labelled **replay** ([guide](replay/README.md)).

## Built during the hackathon

Building period: **26 October–4 November 2026**. Project-authored work includes `lookandpick/` (world state, safety, tools, adapters, routing, recovery, Deck and workspace editor), `scripts/`, the iPhone companion, launcher, and simulator bridge/UI/tests. It also includes workspace reconstruction orchestration, the Gaussian training/export loop, Splat Studio developer tool and SAM Prompt interface. Upstream models, libraries, fonts and robot assets were not built here. Evidence: [PROGRESS.md](PROGRESS.md), its “Built during the hackathon” table and dated implementation entries.

## Open source we build on

| Component | Role and licence |
| --- | --- |
| [SAM 3.1 / SAM 3](https://github.com/facebookresearch/sam3) (Meta) | Local segmentation/tracking; custom **SAM License**. **Pathfinder acknowledges use of the SAM Materials.** No SAM weights distributed here. |
| [MapAnything](https://github.com/facebookresearch/map-anything) (Meta) | Workspace geometry; Apache 2.0 source and separate `facebook/map-anything-apache` weights variant. Exact cached revision/checks: [licences](docs/licences.md). |
| [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) (Google) | Fast CPU hand gate; source Apache 2.0, model terms checked separately in the licence table. |
| [EfficientDet-Lite0](https://www.kaggle.com/models/tensorflow/efficientdet) (Google) | Person boxes in browser RGB monitoring, distinct from SAM's Deck feed; model licence verification in the table. |
| [gsplat](https://github.com/nerfstudio-project/gsplat) | Gaussian rendering/densification, Apache 2.0. |
| [LeRobot](https://github.com/huggingface/lerobot) (Hugging Face) | SO-101 commissioning/hardware support, Apache 2.0; no Deck physical motion acceptance. |
| [SO-101 URDF/meshes](https://github.com/TheRobotStudio/SO-ARM100) (community) | Simulation/visual robot, Apache 2.0; pinned source and upstream licence in `simulation/assets/so101/`. |
| [cuRobo](https://github.com/NVlabs/curobo) (NVlabs) | Planned only: **NOT integrated**. Apache 2.0 upstream; our bounded route planner is separate. |
| [MuJoCo](https://github.com/google-deepmind/mujoco) | Simulator, Apache 2.0. |

See [the full model/library licence inventory](docs/licences.md) for supporting libraries, optional tools, Wearables SDK, fonts and attribution. Model weights/caches are excluded from Git; vendored robot assets and fonts retain upstream licences. [Checkpoint provenance](docs/dependency-provenance.md) records the 3 Nov cache check.

## Meta tooling used, by name

- **SAM 3.1**, local: workspace masks and slower live people/hand segmentation.
- **MapAnything**, local: workspace geometry and camera estimates.
- **Muse Spark 1.3**, API (`muse-spark-1.3`): interpretation/orchestration through five validated tools.
- **Muse Voice Transcribe 1.0**, API (`muse-voice-transcribe-1.0`): spoken instructions.
- **Meta glasses**: microphone via the iPhone app; Liam heard spoken replies in the glasses. Companion uses Meta Wearables DAT 1.0.1.

**Glimmer, Spark Open, Wonder, Image and V-JEPA 2 were NOT used.** Earlier plans are not completed integrations. Evidence: 4 Nov “SPOKEN commands…” and implementation log in [PROGRESS.md](PROGRESS.md); identifiers in [model access](docs/model-access.md).

## Run it yourself in five minutes with no keys or hardware

Needs Python 3.11+ with compatible MuJoCo wheels and working OpenGL/EGL; Python 3.14 was used here. Five minutes is an aim on an already suitable machine, not a measured fresh-install guarantee. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
python3 -m venv simulation/.venv
simulation/.venv/bin/python -m pip install -r simulation/requirements.txt
python3 simulation/start.py
.venv/bin/python -m lookandpick.mission_control --backend sim --model fake
```

The final command is `python -m lookandpick.mission_control --backend sim --model fake` in the project environment. Open [Deck](http://127.0.0.1:8765); the [simulator bridge/workbench](http://127.0.0.1:8766) runs separately. Use the command panel for a simulated pick/place. `fake` is scripted, not a Meta call. No keys, cameras, model weights or physical arm needed; no live camera safety demonstration implied. EGL may use a GPU for rendering; this is not a CPU-only promise.

Core checks: `.venv/bin/python -m pytest`. All configured suites:

```sh
scripts/test_all.sh
```

The full script requires `workspace_splat/.venv` ([setup](workspace_splat/README.md)); it runs MuJoCo checks when `simulation/.venv` exists. **JavaScript tests need `node` on PATH.** Missing suites must not be counted as passed. This requires more setup than the minimal demo. Stop the SAM worker before tests/reconstruction if it holds the GPU lease. Current live commands/environment split: [demo guide](docs/demo-2026-11-04.md), [hand safety](docs/hand-safety.md), [iPhone](mobile/PathfinderGlasses/README.md), [launcher](docs/launcher.md). Keys belong in ignored `.env`, never Git.

## Safety rules

From [CLAUDE.md](CLAUDE.md):

- Safety-critical checks run locally and never depend on the network.
- Prefer speed-and-separation (slow as people approach, stop at minimum distance) over hard pauses.
- “Stop” bypasses everything.
- **Never command the real arm from a remote/unattended session.** Sim, replays and code are fine remotely; anything that powers the servos needs Liam physically present with the 5V kill switch in reach.

## Known limits and honest gaps

- No live Cartesian real-arm pipeline acceptance or certified safety claim. Physical full-arm geometry, stopping distances and camera-to-servo latency remain unverified.
- SAM/MediaPipe disagreements have been observed but not resolved against ground truth. Cameras cannot certify empty 3D space; voxel freshness is not the watchdog. Only manual cubes are hard map rules in these simulated moves; unknown display cubes do not themselves gate them (4 Nov “no-go cubes enforced…”).
- The planner checks the tip path, not full-arm clearance. A route around a hand helps only when the hand is clear of the whole arm; otherwise simulator link checks hold it until the hand leaves. Waiting then carrying on is the dependable shot (4 Nov motion tasks).
- Marker black square confirmed 120 mm; checkerboard readings about 3% over nominal (15.4 × 15.7 mm versus 15 mm in the evening re-scan). Board print/scale offsets and off-marker accuracy remain caveats. Run-view marker-plane texture and visual foot/pan offsets are visual alignment fixes, not physical revalidation.
- Phone-photo reconstruction worked; glasses video reconstruction and real-photo “this one” remain unverified. Recognition duplicated words; noisy-room and mid-clip disconnect acceptance remain pending.
- Live grasp orientation on real cameras/objects, concave objects and physical wrist roll remain gaps. Four-view reconstruction can exhaust the 8 GB GPU; scan quality is not a safety certificate.

Evidence: 4 Nov live camera, motion, grasp, spoken-glasses, re-scan and Run-view entries in [PROGRESS.md](PROGRESS.md).

## Licence

**No licence for Pathfinder's own code is present at the repository root. Liam needs to choose one before describing this as a licensed open-source release.** Public visibility alone does not grant reuse rights. Upstream licences apply to their materials and do not license this project's original code; see [licences and outstanding checks](docs/licences.md).
