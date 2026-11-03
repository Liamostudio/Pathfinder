# Codex integration handover — 3 November 2026

Requested work follows `docs/codex-tasks-2026-11-03.md` in order. Physical safety milestones remain unticked. Claude-owned runtime, hand-gate, model, kinematics, camera and hardware adapter files were not edited.

| Task | Delivered and verified | Limits |
| --- | --- | --- |
| 1. Commit existing work | `b8c0240` Gaussian/SAM tooling and licence provenance; `6031d3a` persistent main-app workspace/voxel integration. Pinned cached MapAnything metadata declares Apache 2.0. | Weights, original photos, caches, keys and physical calibration captures excluded. |
| 2. Deck on Runtime | `--backend sim`, `--model fake/spark/glimmer`, async command jobs, independent STOP, CallLog timeline with latency/cost/tokens, simulator schematic and saved workspace switch. Free scripted HTTP/GUI pick/place passed. Approved Spark HTTP run picked, placed and spoke; 3 model calls before STOP test. | Sim scene uses ground truth unless a separately configured Runtime provides vision. An in-flight provider response cannot be cancelled; the stopped run does not continue. The first artificially low call cap was reached after pick/place and before final reply; retest passed. |
| 3. Hand-gate display | Every state, speed bar, distance, hands, fps, p50/p95 and freshness; invalid/missing sources show NOT CONNECTED, observations older than 150 ms show STALE. Direct `runtime.hand_gate_status` or optional loopback status proxy; fake provider/UI cycle tests. | No real camera opened or camera acceptance performed. External status is display-only. Default source is explicitly labelled simulator ground truth. |
| 4. Submission replay | Static `replay/` with one JSON run, embedded copy for offline-folder playback, schematic, timeline, variable-speed player, scrubber and file chooser. Standalone `http.server` playback tested; invalid data displays an error. Runtime recordings export from `/api/replay`. | Ready for hosting; no public deployment/account provisioned. Recorded demo uses a scripted model and injected hands, with placeholder call timing labelled. |
| 5. Read-only arm panel / traces | Explicit open/disconnect, existing-port detection and kernel reservation, no reconnect loop, joint degree/range bars and Rest ticks, voltage warnings, temperature, unknown torque state, shared `real_arm` events. Fake-bus reads/close checks, six-joint sample and fake CSV charts verified. | No serial device opened. Bench validation of readings and contention is pending; the selected Python environment needs the optional hardware dependencies to connect. |
| 6. Measured visual placement | Uses `workspace_bridge.robot_placement`, `BaseFrame.from_calibration` and `urdf_joint_radians`; measured marker defaults 120 mm black square / 160 mm plate. Explicit apply button and automatic placement on new compatible anchoring/rebuild; editable fields retained. Live joint preview is opt-in and clears on stale/disconnected telemetry. | Visual placement only, never a planner or safety input. Existing saved versions are not rewritten. Source-image lens calibration is still unverified in the example. |
| 7. Filming scenario | Run demo generates an isolated seeded MuJoCo pick/place with hand slow/stop/resume and STOP during a second move, then plays the recording. Three independent runs produce identical frames and events. | A repeatable simulation recording, not a live real-camera/hardware safety demonstration. |

## Voxel interaction changes requested during integration

A chosen axis browses whole perpendicular slices. Only the chosen axis button stays visible. Camera-side cells are hidden while picking. Click freezes the layer; further pointer movement picks within that layer. Click selects a cell, double-click toggles its manual restriction without leaving the layer. Back/right-click returns to moving layers, then axes, then vertices. Slider and +/− touch controls work during moving-layer selection and disable while frozen. Existing manual restrictions are preserved; manual unblock cannot erase sensor occupancy or convert unknown space to observed clear. Active and setup viewports use a square aspect ratio (708 × 708 pixels measured in the browser).

## Validation

`PATH=<bundled-node-directory>:$PATH scripts/test_all.sh`: **444 passed, 1 skipped** in the workspace/core environment; **46 passed** in the MuJoCo environment; **11 JavaScript tests passed** across six test files. The separate Splat Studio quality test file also passed. Subsequent focused tests cover Runtime interruption, in-flight model response, hand-gate provider, telemetry reads/close/port-busy, visual placement and frozen picker backtracking. The skipped test is reported by pytest; no physical milestone was inferred from these software results.

Browser: typed scripted command completed pick/place and rendered CallLog metrics; seeded demo generated and played to its final latched STOP; static replay played separately from mission control; sample joint data and per-move trace charts rendered; square canvas and frozen-layer/back navigation verified. Screenshots live in ignored `recordings/` (`pathfinder-square-frozen-layer.png`, `pathfinder-trace-charts.png`, `pathfinder-readonly-sample.png`).

## Run

- Existing workspace GUI: `python -m lookandpick.mission_control --port 8765`.
- Runtime GUI: start `simulation/start.py`, then `python -m lookandpick.mission_control --backend sim --model fake --port 8769`.
- Spark uses `--model spark` and existing `.env`; keys stay on the server. Do not replay paid calls just to repeat an already-passed test.
- Static replay: `python3 -m http.server 8788 --bind 127.0.0.1 --directory replay` or open `replay/index.html` directly.
- Direct live hand gate on the **simulated** arm: `--backend sim --hand-gate 2 --intrinsics calibration/ace-pro.json` in the hands-capable environment. Alternatively `--hand-gate-status-url http://127.0.0.1:8768` reads Claude's external status server for display only.

The voxel path endpoint remains advisory and is not called from motion code. No motor commands, torque controls, powered scripts or physical hardware tests were run by Codex.
