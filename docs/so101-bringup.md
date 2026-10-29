# SO-101 bring-up — Liam at the bench

No real hardware was connected or commanded during this implementation. LeRobot was not
installed; wrapper and health behaviour were tested with fake buses. The current GUI still
uses `SimArm` exclusively.

## Tonight's checklist (read this first)

1. **Do not use the `lerobot-setup-motors`, `lerobot-calibrate` or `lerobot-teleoperate` commands on this machine.** They crash on Python 3.14 (`TypeError: str | None is not callable`, from LeRobot's argument parser). Use `scripts/so101_setup.py` instead, run with the LeRobot environment: `~/src/lerobot-venv/bin/python scripts/so101_setup.py ...`. It makes the same two method calls LeRobot's scripts make, with the same prompts.
2. Multimeter: ~5V at the adapter terminals **before** the URT board is connected. Confirm in the SO-101 / LeRobot guide that 5V is the right supply for the 7.4V (1:345) motors and that the URT board accepts it.
3. Plug the URT board in with **no motors connected**: `~/src/lerobot-venv/bin/python scripts/so101_setup.py find-ports` should list one new `/dev/ttyACM*`.
4. One motor at a time, in the order the script prompts: `setup-motors` (below). Label each motor with its ID as you go.
5. Assemble. Then `calibrate`, then `python scripts/so101_check.py --port ...` (read-only voltage/temperature/ID check).
6. **Stop there for tonight.** Moving the arm to a Cartesian target needs FK/IK for the real arm, which does not exist yet (see "Adapter boundaries"). Only the sim has IK. The first real motion should be a few degrees on one joint, slowly, with the kill switch in hand.
7. Keep a hand on the kill switch whenever torque is on. Halt disables torque, so support the arm.

## Power and motor IDs

Use this project's **5V 10A supply only (5.5 x 2.1 mm, centre-positive); never exceed 7.4V**. Read ~5V with a multimeter at the adapter terminals before connecting the URT board. Liam must be physically present,
with the 5V kill switch in reach whenever servos are powered. Do not run setup, calibration
or motion through an unattended/remote session.

Before assembly, connect **one motor at a time** to the URT board. New motors may all have
the same factory ID; do not connect all six and attempt to configure them together.

| ID | Joint |
| --- | --- |
| 1 | shoulder_pan |
| 2 | shoulder_lift |
| 3 | elbow_flex |
| 4 | wrist_flex |
| 5 | wrist_roll |
| 6 | gripper |

Use a separate LeRobot-compatible environment; the optional `hardware` extra declares
`lerobot[feetech]` but compatibility has not been verified on this Python 3.14 machine.
Follow the [official SO-101 setup](https://huggingface.co/docs/lerobot/so101) to identify the
port and assign IDs/baud rates. For the follower, its setup command is:

```sh
~/src/lerobot-venv/bin/python scripts/so101_setup.py setup-motors --port /dev/ttyACM0
```

Replace the example port with the actual board port. Follow the prompts, exchanging the
single connected motor as instructed. This command writes motor configuration; it is not
the read-only health check below and was not run by Codex. Label motors, then assemble and
perform LeRobot's manual range calibration with Liam present before considering motion:

```sh
~/src/lerobot-venv/bin/python scripts/so101_setup.py calibrate --port /dev/ttyACM0
```

## Read-only health

```sh
PYTHONPATH=. ~/src/lerobot-venv/bin/python scripts/so101_check.py
PYTHONPATH=. ~/src/lerobot-venv/bin/python scripts/so101_check.py --port /dev/ttyACM0
```

Without `--port`, only path names are listed: no port is opened. With a port, the helper
uses LeRobot's raw `SerialMotorsBus`, opens with `handshake=False`, reads ID, voltage and
temperature registers, and disconnects with `disable_torque=False`. It never calls follower
`connect`, configures motors, enables/disables torque or submits positions. Errors are
reported per register/motor and produce a failing exit status. STS voltage is reported
both raw and interpreted in tenths of a volt; verify the interpretation against the actual
firmware and supply before relying on it. This was tested with fake register values only.

Do not replace this with `SO101Follower.connect()` as a read-only shortcut. That method
configures the bus/motors and can invoke calibration. Its disconnect can also write torque.

## Adapter boundaries

`SO101Arm` implements `move_to`, `grip`, `halt` and `set_speed_scale`. Its default dry-run
logs bounded simulated steps, never reads/writes an injected bus and never imports or
connects LeRobot automatically. `preview_step` works without hardware or kinematics.
Cartesian moves require supplied inverse and forward kinematics; there is no invented
mapping from XYZ to servo angles. Missing kinematics returns a refused action.

Development ceilings (profiles can narrow joint ranges, not widen them):

- Body limits: shoulder pan ±60°, shoulder lift ±45°, elbow flex ±60°, wrist flex ±45°,
  wrist roll ±60°. Gripper is in LeRobot's calibrated 0–100 units, restricted to 0–20 here.
- Command velocity ceiling: 15 degrees/s (gripper: 15 normalised units/s), 30 Hz default;
  at most 1 degree/unit per command. Dry-run advances virtual steps without wall-clock sleeps.
- Workspace: x/y −0.30 to +0.30 m, z 0 to 0.40 m; requested endpoints and intermediate FK
  poses must stay inside. This checks the end effector, not all robot links or swept volume.
- Gripper torque ceiling: **100 raw units**, set through `Max_Torque_Limit` for live mode.
  It is not a measured force cap in newtons. Gripper force versus posture/contact must be
  measured at the bench; there is no force sensor feedback in this implementation.

These numbers are development constraints, not commissioned safe ranges. Real mode refuses
unless all of the following are supplied: `dry_run=False`, `i_am_present=True`, a local
interactive stdin/stdout, the exact confirmation `5V KILL SWITCH READY`, a commissioned
limits profile, FK/IK and an already connected/calibrated degree-mode follower bus. SSH
environment markers are refused. No motion CLI or GUI route exposes live mode.

`LeRobotFollowerBus` wraps a caller-owned follower. It reads joint feedback directly rather
than waiting on cameras. Submitted targets are not completion: the adapter requires observed
joint positions to converge before returning success. Timeouts/errors latch stop. Stop
sets its latch before taking the short dispatch lock; it does not wait on the action lock,
and no later move/grip can clear it. A zero live speed scale latches halt rather than leaving
a position servo travelling toward its last target. Dry-run hand pauses can resume.

Live halt disables torque, which may let an unsupported arm fall. That behaviour, serial
I/O deadlines, sensor quantisation/tolerances, physical velocity, force and stopping distance
all need bench validation before `commissioned=True` is truthful. The current numerical
completion tolerance is untested with real servo feedback. Do not enable live mode simply
to get past a refusal. No actual SO-101 FK/IK or physical profile is supplied in this repo.

Primary implementation references: [SO follower source](https://github.com/huggingface/lerobot/blob/main/src/lerobot/robots/so_follower/so_follower.py),
[motor bus source](https://github.com/huggingface/lerobot/blob/main/src/lerobot/motors/motors_bus.py),
[robot API](https://huggingface.co/docs/lerobot/main/api/robots).
