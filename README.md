# Look-and-Pick

A table-mounted SO-101 robot arm that works safely around people. Meta's models see, understand and decide; a simple, checkable safety layer enforces. Models never drive the motors directly.

Entry for the Meta Global AI Developer Hackathon (indie track, "Push the Frontier").

## Built during the hackathon

New work started on **26 October 2026**, straight after the kickoff call. The first commit in this repo is the start point; nothing here predates it.

- World state, safety layer (speed-and-separation, stop latch) and the orchestrator tool surface
- Adapter interfaces with fake implementations for development against recorded footage
- Scripts to run SAM 3.1 on the 8 GB laptop GPU: a memory and speed test, and a webcam preview that tracks a text prompt (`scripts/`)
- ArUco table calibration with synthetic-image tests; real-camera validation still pending
- Deck mission control GUI: simulated scene, safety scenarios, pick/place controls, preview media and session timeline

## Open source we build on

- [cuRobo](https://github.com/NVlabs/curobo) — motion planning
- [LeRobot](https://github.com/huggingface/lerobot) — SO-101 control
- SO-101 URDF
- [SAM 3 / SAM 3.1](https://github.com/facebookresearch/sam3) (Meta) — text-prompted segmentation and tracking; weights from Hugging Face (`facebook/sam3.1`, gated, not in this repo)

## Safety

- Safety checks run locally and never depend on the network.
- Slow as people approach, stop at minimum distance.
- "Stop" bypasses everything.
- The real arm is only ever powered with a person present and the 6V kill switch in reach.

The current implementation checks before simulated moves and gripper actions. Continuous
hand monitoring, detector-health checks, full-arm geometry and a real hardware adapter are
still to be built. See the [code audit](docs/audit-2026-10-27.md) for remaining requirements.

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
available, with no detection applied. Models and real hardware are not connected.

See the [mission control guide](docs/mission-control.md) for controls, media formats and integration points.
