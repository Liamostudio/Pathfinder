# Real-arm hand-stop: bench checklist (4 Nov 2026)

Goal: show the REAL SO-101 freeze when a hand comes near, then fold back. It does not pick anything up. Software: `scripts/so101_hand_stop_demo.py` and `lookandpick/real_arm_demo.py`. **Everything below is run by Liam, at the bench, in a local terminal, with the 5V kill switch in reach.** Nothing in this repo powers the servos from a remote or unattended session.

Status (4 Nov, evening): built and tested with a fake bus and fake hands (`tests/test_real_arm_demo.py`, 52 tests), then run on the real arm with Liam at the bench: slow/freeze/resume with a hand worked at 8 deg/s. Not yet verified: the final fold-and-release completing after the settle fix; stop distance and latency are unmeasured. See `PROGRESS.md` for exactly what was seen.

One thing to remember: the Deck must be running a build whose `hand_gate` status has `positions` (the live Deck runs from `pathfinder-film2`, patched but uncommitted there). Without it the script reports BLIND and refuses to move.

## What it does
- Options in the order you will want them: `--continuous --amplitude-cm 2 --speed 0.8 --duration 30 --stop-cm 10`.
- Sweeps ONE joint (shoulder pan) about 3 to 4 cm either side of where the session starts, at most 3 deg/s (commissioned ceiling is 10).
- Hand near the tip path: slows (50 cm to 10 cm), then at 10 cm FREEZES (`--stop-cm`; measured from the hand's surface, centre minus its 14 to 20 cm radius): every goal is set to where the arm is, torque stays on, so it holds still instead of going limp.
- Resumes 0.3 s after the way has been clear. Held more than 30 s: gives up and folds once the hand has gone.
- Folds back to the pose the session started in, slowly (at most 5 deg/s), hand-checked every step, stopping 2 degrees inside each calibrated end stop. Torque is released only when folded (the arm then settles the last few degrees onto its rest).
- Cannot see (Deck down, answer older than 0.25 s, gate stale, malformed data): freezes, does NOT fold. Press `f` once it can see again, or cut the supply.
- Supply collapse (below 3.8 V, or under 4.5 V three reads in a row), bus error: releases torque as the last resort and tells you to cut the 5V supply. A joint that stops following its command: holds still and waits for you. A single failed bus read is skipped; 5 in a row release torque.
- Options: `--continuous` sweeps until you press `q`/`f` (instead of `--cycles`); `--work-area X0 X1 Y0 Y1` (marker-frame metres) freezes the arm whenever a hand is inside that rectangle, however far from the tip.
- Keys: SPACE stop, `r` resume, `f` fold now, `q` fold then exit, `x` release torque (arm goes limp: support it). Ctrl-C = SPACE; twice within 3 s releases.

## Honest limits
- The hand check depends on loopback HTTP from the live Deck (it owns both cameras; a second process cannot open them). It fails closed, but it is not an in-process gate like the simulated arm's. The 10 cm stop distance (chosen on the bench, `--stop-cm`) is not a measured safe distance.
- The fold is joint-space and hand-checked against a straight tip line, not a collision-checked path. It assumes the start pose is the folded rest pose.
- The sweep is refused (before any torque) if the start pose would put either end outside the pan range or the commissioned workspace box. If so, move the arm by hand to a pose nearer the middle of the pan range and try again.

## Order (stop at any step that is not clean)
1. **Supply:** multimeter on the adapter terminals, about 5 V (5 V 10 A, 5.5 x 2.1 mm centre-positive; never above 7.4 V). Only then connect the URT board and USB.
2. **Deck:** live Deck running on 8765 with both cameras (hands environment). In the Deck's real-arm panel make sure it is DISCONNECTED: only one process may hold the serial port.
3. **Read-only check** (torque off, nothing moves): prints positions, voltages, whether the Deck's hands are fresh, and the sweep plan.
   ```bash
   ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower --check
   ```
4. **Hold test** (torque on, nothing moves, 10 s): watch the voltage. Stop if it sags near 4.5 V.
   ```bash
   ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower --hold-only
   ```
5. **Tiny first sweep, hand test:** smaller and slower first, then the defaults.
   ```bash
   ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower --amplitude-cm 2 --speed 0.15 --cycles 1
   ```
   Passes when: it sweeps; your hand coming in slows it then freezes it with torque held (status shows `frozen`, scale 0.00); it resumes about 1 s after you leave; SPACE freezes it at once; covering a camera or stopping the Deck freezes it and says `cannot see`; letting it finish folds it and releases. Then the default sweep (`--cycles 2`, 4 cm, 0.3).
6. **Film** only after step 5 passes. Optional: `--status-port 8790` serves the demo's gate state on loopback for Deck's hand-gate panel.

If anything looks wrong: kill switch first, software second.
