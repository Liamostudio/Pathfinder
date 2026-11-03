# Unifying Pathfinder (3 Nov): what exists, how it fits, what to do before the freeze

Written by Claude after reading `PROGRESS.md`, `docs/voxel-working-area.md`, `docs/local-workspaces.md`, `lookandpick/workspaces/`,
the marker alignment and robot code, and running the test suites. Features freeze **today** (CLAUDE.md) and submission is
**2 am UK Thu 5 Nov**, so everything below is wiring and finishing, not new capability. Cut lines at the bottom.

## 1. What exists

| Piece | Owner | State | How I know |
| --- | --- | --- | --- |
| Hand gate: watchdog, slow/stop, live camera loop, Pathfinder Vision | Claude | Works live at about 29 fps on the real camera; lens not yet calibrated; not in Deck | tested live 28 Oct |
| Model layer: Spark orchestrator, call log, runtime | Claude | Runs on the sim with real Spark calls; not in Deck | live runs 28 Oct |
| MuJoCo sim + its own workbench (:8766) | Codex | Works; scenario runner 10/10 | tests, 45 pass |
| Deck / mission control (:8765) | Codex | Hard-wired to FakeTracker and SimArm; now also serves the workspace app | code read |
| Real arm: bring-up, hand tools, calibration, kinematics, commissioned profile | Claude | Every joint moved 20 deg under power; tip accurate to about 2 mm in height; planning verified in software only | traces, 14 + 17 + 17 tests |
| Workspace reconstruction + voxel editor | Codex | Reconstruct to Ready, edit, save, voxel states | Codex's report; I ran the Python suite (399 pass), not the GUI, not node |
| Hosted replay build ("live product") | nobody yet | not started | PROGRESS |

## 2. What I found in Codex's work

Good: it reuses my calibration code, so **the marker frame is the same everywhere**: `marker_alignment.py` builds on
`TableCalibration` (origin at the black square's centre, +X right, +Y up, z up). Voxel `marker_metres`, my `BaseFrame`, the sim's
world frame and the touch calibration are one frame. Unknown is red/amber and green needs real clear-space evidence, which is
the right call. The suite is healthy.

Risks to deal with:
1. **Nothing of Codex's is committed.** `gaussian_studio/`, `lookandpick/workspaces/`, `lookandpick/web/*`, `gpu.py`, `sam_web/` are
   untracked, and `README.md`, `pyproject.toml`, `mission_control.py`, `web/app.js`, `web/index.html` are modified in the working tree.
   Whoever pushes must not sweep these in by accident, and the repo is the submission. Codex should commit its own files in clear commits.
2. **`node` is not installed on this laptop**, so the four JavaScript test files (cube interaction, footprint, voxel maths) are
   unverified here. `scripts/test_all.sh` now says so instead of skipping quietly.
3. **Two front ends**: `gaussian_studio` ("Splat Studio") and the Pathfinder workspace screens. Pick the demo path (Pathfinder :8765) and
   treat Splat Studio as a developer tool.
4. **Voxel freshness is not the safety watchdog.** The voxel map's 500 ms and 750 ms windows are display logic. The only safety path is the
   hand gate (150 ms watchdog, latched stop). The voxel doc already says the path check never authorises motion; keep it that way.
5. **Licences**: MapAnything, the splat trainer, MediaPipe and EfficientDet-Lite0 belong in the README's "Open source we build on", and I have
   not checked the licence of the cached MapAnything weights.
6. **GPU lease**: anything of mine that loads SAM (the live SamTracker, not built) must take `lookandpick/gpu.py`'s lease, or it fights the
   reconstruction for the 8 GB card.
7. **Marker size**: both the reconstruction scale and my base-frame fit assume the black square is exactly 120 mm. One ruler measurement
   (the black square only, not the plate) should be entered in both places.

## 3. The seams, and what I built for them today

- **Robot pose in the scene.** The editor places the SO-101 by hand ("illustrative"). I now have the measured base frame. `lookandpick/workspace_bridge.py`
  `robot_placement(canonical_from_marker, BaseFrame)` returns the editor's `translation`, `rotation` and `scale` for the real base position, tested against
  Codex's own `robot_transform` in four orientations and by checking that the tip lands at the same scene point either way. `urdf_joint_radians` turns real
  readings (with the calibration offsets) into the angles the editor draws. **Wiring these into `compose.py` and the editor is Codex's job** (brief updated).
- **One test command**: `scripts/test_all.sh` (workspace env 399, sim env 45, JavaScript skipped without node).
- **Arm side (already done)**: calibrated FK/IK, `BaseFrame`, commissioned limits, marker-frame arm wrapper, dry-run planner (`scripts/so101_plan.py`).
- **Still missing on the arm side**: the live session wrapper (safe start, supply watch) that makes the real arm accept a Cartesian move.

## 4. Plan, in order

| # | Task | Owner | Needs |
| --- | --- | --- | --- |
| 1 | Commit Codex's own files in clear commits; keep README honest (built here vs open source) | Codex | none |
| 2 | Deck on the sim backend with the orchestrator and call-log timeline (Codex brief item 5); I expose a small runtime surface for it | Codex UI, Claude runtime | none |
| 3 | Hand gate visible in Deck (state, decision, latency) from the live loop | Claude runtime, Codex UI | camera |
| 4 | Real-arm session wrapper, then the first small Cartesian move on the real arm | Claude | Liam present, kill switch, his go |
| 5 | Wire `robot_placement` and calibrated joint display into the workspace editor (status becomes `touch_calibrated`) | Codex | task 1 |
| 6 | Real-arm read-only panel and trace viewer (existing brief) | Codex | none |
| 7 | Hosted replay export of one recorded run (the "live product") | Claude data, Codex page | task 2 |
| 8 | Measure the black square; lens calibration of the Ace Pro; README; 3-minute video; submit | Liam, all | none |

Cut lines, in order (CLAUDE.md): multi-step tasks, obstacle rerouting, target confirmation, second camera. Also cut before touching the core: voxel-to-hand-gate
bridging, manual no-go zones feeding the planner, Glimmer, voice, Muse Wonder. **Never cut:** hand safety, one camera, one clean pick (the sim pick is the
guaranteed one; the real arm is the stretch for the video).

## 5. Decisions needed from Liam

1. Hero shot for the video: sim arm picking through Spark with the hand gate (guaranteed), or the real arm (needs task 4 and a clean first move)?
2. Is the workspace scan and voxel editor in the demo, or a short "scene understanding" cutaway?
3. Who commits and pushes Codex's files, and when (task 1 gates everything)?
