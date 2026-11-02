# Codex brief: real-arm status in Deck, plus what happened on the arm today (2 Nov)

Read first: `CLAUDE.md` (safety rules are non-negotiable), `docs/so101-bringup.md` (Rest pose, joint directions,
supply findings), `PROGRESS.md` (log entries for 30 Oct and 2 Nov). Commit only your own files, update `PROGRESS.md`
honestly, tick nothing without a passed test. Do not edit `scripts/so101_*.py`, `lookandpick/adapters/so101.py`,
`lookandpick/models/`, `calibration.py` or `intrinsics.py` (Claude owns them); ask for what you need from them.

## The one hard rule

**Deck never powers or moves the real arm.** Everything powered (`so101_setup.py`, `so101_nudge.py`, `so101_jog.py`)
runs only from a local interactive terminal with Liam present and the 5V kill switch in reach, and refuses over SSH.
Deck may show read-only telemetry and recorded traces. No button, endpoint or scenario in Deck may send a goal
position, enable torque or start those scripts. Also: only one process can hold `/dev/ttyACM0`, so the telemetry
reader must not run while a powered tool does (see "Port ownership").

## What is true now (the follower arm, as built and tested today)

Built and wired (SO-101 follower, six STS3215 7.4 V 1:345 servos, Feetech URT-2 board on `/dev/ttyACM0`, 5V 10A supply).
Verified by the read-only check and by Liam's multimeter:

- All six motors answer under IDs 1 to 6 (shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper) at
  5.4-5.5 V and 21-27 C. Calibration saved (LeRobot manual calibration, id `follower`) at
  `~/.cache/huggingface/lerobot/calibration/robots/so_follower/follower.json` (outside the repo). Spans: shoulder_pan 147,
  shoulder_lift 208, elbow_flex 195, wrist_flex 207 degrees, wrist_roll full turn, gripper 131.
- Supply: 5.54 V unloaded at the adapter, 5.50 V with a servo connected (multimeter). Torque-on, staggered motor by
  motor, now holds 5.3-5.4 V. Earlier, torque-on dipped to 4.1 V for ~2 s (wiring since thickened; cause not isolated).
- Powered moves that completed: wrist_roll +3 and +15 and +20 degrees and back (lag about 1 degree, lowest 5.0-5.2 V).
- **Not working yet:** wrist_flex + (tips the gripper down) stalled against the table from Rest; the shoulder_lift and
  elbow_flex repeatedly "stopped following" when jogged from Rest even at full torque, and the supply sagged to 4.3 V
  under load, tripping the 4.5 V guard. The elbow looks mechanically blocked (gripper tip on the table, or cable loops,
  or a printed part); the shoulder lift lacks range. 5 V is marginal for 7.4 V servos lifting the folded arm; a 6 V
  (never above 7.4 V) supply is the proper fix and is not yet bought. One session also lost the controller (USB).
- No real-arm FK/IK yet, so Cartesian moves are refused. The marker-to-arm-base offset is not measured
  (the sim's `ARM_BASE_OFFSET` is still the placeholder (0, -0.12, 0)).

Conventions (degrees as the tools print them, centred on each joint's calibration midpoint): Rest = arm folded back on
itself, gripper closed pointing down just above the table (pan about -33, lift about -103, elbow about +98, wrist_flex
about 74, gripper about 1.3; wrist_roll free). At Rest the shoulder_lift sits at its minimum and the elbow at its
maximum, so from Rest the lift can only go positive and the elbow only negative. wrist_flex + tips the gripper down.

## Tools (all terminal-only, for context; do not call them from Deck)

- `scripts/so101_nudge.py --port /dev/ttyACM0 --id follower --moves wrist_roll:20,wrist_flex:-20 [--torque-limit 1000]
  [--trace traces/x.csv] [--hold-only]`: holds every joint, then moves one at a time at 10 deg/s, never past the calibrated
  range; aborts and releases on a stall or a supply sag (3 low readings in a row, or below 3.8 V at once).
- `scripts/so101_jog.py --port ... --id follower [--probe]`: Xbox controller jog with a dead-man (hold LB), prints the
  range each joint reached however the session ends.
- Read-only: `PYTHONPATH=. python scripts/so101_check.py --port /dev/ttyACM0` (JSON: per joint `id`, `voltage_raw`
  (tenths of a volt), `temperature_c`, `errors`), and `lookandpick/adapters/so101_health.py` (`ReadOnlySO101Health`).
- Trace CSV from `--trace`: one row per 50 ms tick with `t_s, move, joint, command_deg, observed_deg` and
  `v_shoulder_pan ... v_gripper` (volts per motor). Liam is about to record a sequential test and film it.

## What to build (GUI side), in priority order

1. **Real arm panel (read-only).** A view in Deck, clearly labelled READ-ONLY / NOT CONNECTED / LIVE, showing per joint:
   present position (degrees) against its calibrated range as a bar (marker at Rest), voltage (colour: normal
   at 5.2 V and above, warn below 4.8 V, alarm below 4.5 V), temperature, torque state if known, and any
   error text. Source: a small read-only poller built on `ReadOnlySO101Health` (extend it with a position read in your own
   adapter file if needed; reads only, never `connect()` on a follower, which configures and writes motors).
2. **Port ownership.** The poller must open the port only when asked, close it fully, and show "port busy" instead of
   crashing when a powered tool holds it. Never auto-reconnect in a loop that could race a powered tool.
3. **Trace viewer.** Load a `--trace` CSV and plot voltage per motor against time and against the moving joint's
   observed angle, one chart per move, with the lowest point annotated. This is the evidence Liam is gathering for
   "where does the supply sag" (wiring, supply, or a mechanical block).
4. **Rest and conventions card** showing the joint directions above, so a person reading Deck knows which way each
   joint moves.
5. **Timeline entries** for arm events in the existing event shape (`source: "real_arm"`): connected/disconnected,
   under-voltage warnings. Do not invent a second schema; reuse `CallLog` keys where they apply.

## Acceptance

- With no arm attached, the panel shows NOT CONNECTED without errors, and a recorded sample (use the fake bus the
  tests use, or a saved `so101_check` JSON) renders every joint.
- A trace CSV from a fake run renders the charts; a malformed CSV shows an error, not a blank page.
- A test proves no Deck code path imports or calls a writing method (`write`, `sync_write`, `enable_torque`,
  `Goal_Position`, `Torque_Enable`) on the real-arm bus.
- `PROGRESS.md` says what was tested and how; milestones untouched.
