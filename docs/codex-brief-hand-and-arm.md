# Codex brief: hand detector and real-arm adapter (sim-safe)

Context: read `CLAUDE.md` (safety rules are non-negotiable), `PROGRESS.md`, `lookandpick/safety.py`,
`lookandpick/adapters/base.py`. Claude is working in parallel on camera/lens calibration
(`lookandpick/calibration.py`, `intrinsics.py`, `scripts/`): **do not edit those files**. Commit only your
own files; update `PROGRESS.md` honestly in the same commit (tick nothing without a passed test).

## Task A: fast-loop hand safety (highest value, never cut)
1. A `HandDetector` adapter protocol plus a lightweight implementation that runs at 15-30 Hz on the Legion
   CPU/GPU without SAM (SAM 3.1 is ~2.8 fps). Try MediaPipe Hands if it installs on Python 3.14, otherwise
   an OpenCV/ONNX palm detector. Keep it behind the adapter so a fake can stand in; heavy deps go in a new
   optional extra, not core dependencies. Develop against synthetic frames and recorded clips (`ReplayCamera`).
2. Convert detections to table-frame positions via `TableCalibration.pixel_to_table(..., z=hand_height)`
   (call it, don't change it) and feed `world.py` hands.
3. Continuous monitoring: a loop that re-checks hand-vs-arm-path every frame, slows with distance, stops at
   the minimum distance, and **fails safe when the detector stalls or dies** (no frames for N ms => stop/slow).
   This closes the gaps listed in `docs/audit-2026-10-27.md` ("continuous monitoring, detector-health").
4. Tests: hand approaches path, leaves, detector timeout, stop latch bypasses everything, latency budget.

## Task B: SO-101 hardware adapter, dry-run only
1. `adapters/so101.py` implementing the arm adapter interface over LeRobot's SO-101 follower. LeRobot is
   not installed and no servos are connected: import it lazily and test everything with a fake bus.
2. Hard limits baked in: per-joint position limits, velocity cap, gripper force cap, max step per command,
   workspace box. Default is **dry-run (logs, never sends)**. Real motion needs an explicit
   `--i-am-present` style flag plus an interactive confirmation that the 6V kill switch is in reach, and
   refuses if not running in an interactive terminal (no remote/unattended driving, per CLAUDE.md).
3. A `scripts/so101_check.py` read-only health check (ports, motor IDs, voltages, temperatures) that sends
   no motion commands. Document the motor-ID setup steps for Liam in `docs/so101-bringup.md` (set each
   motor ID one at a time before assembly, 6V supply only, never above 7.4V).
4. Tests with the fake bus: limits clamp, dry-run sends nothing, refusal when not interactive, stop bypasses.

Do not touch real hardware. Report what you could not verify.
