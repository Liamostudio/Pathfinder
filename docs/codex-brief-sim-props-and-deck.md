# Codex brief: sim props, grasp, shared kinematics, Deck on the sim (28 Oct)

Context: `docs/plan-2026-10-28.md` (Track C), `simulation/BRIEF-CHECK.md` (your own gap list), `CLAUDE.md`
(safety rules; supply is now 5V). Claude is building `lookandpick/models/` (orchestrator, Muse Spark
client, voice) and `lookandpick/runtime.py` in parallel: do not edit those, `calibration.py`,
`intrinsics.py` or `scripts/calibrate_lens.py`. Commit only your own files; update `PROGRESS.md`
honestly; tick nothing without a passed test. No real hardware.

In priority order:

1. **Named props** in the sim: mug, apple, bottle and a light prop, with textures good enough for SAM 3.1
   to segment from a text prompt (colour and shape, not flat grey boxes). Seeded placement, labels in
   `snapshot()`. Claude needs this for the language-to-target work first.
2. **Camera frames over the bridge** as `FrameSource`s (both fixed cameras, 1280x720 option) so a separate
   SAM process can consume sim frames. Include camera pose/intrinsics and matching ground-truth object
   positions, so mask-to-table error can be scored.
3. **Shared kinematics module** (FK/IK from the SO-101 URDF) used by the sim arm and importable by
   `adapters/so101.py`, so the real arm follows the same planner as the twin.
4. **Contact grasp** replacing the ideal attachment, plus STS3215 lag/torque limits and a reduced-torque
   option for the 5V supply. Keep the scenario runner green.
5. **Deck on the sim:** a backend switch so `lookandpick.mission_control` can run on `SimulationClient`,
   and a model-call timeline that accepts entries as JSON `{name, input, output, latency_ms, cost_usd,
   timestamp}` (Claude's call log will emit this shape; keep the schema in one documented place).
6. Finish the camera/detector fault matrix from `BRIEF-CHECK.md`.

Report what you could not verify (render rate alongside SAM on the 8 GB card, grasp reliability).
