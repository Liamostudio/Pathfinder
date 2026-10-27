# Look-and-Pick

A table-mounted SO-101 robot arm that works safely around people. Meta's models see, understand and decide; a simple, checkable safety layer enforces. Models never drive the motors directly.

Entry for the Meta Global AI Developer Hackathon (indie track, "Push the Frontier").

## Built during the hackathon

New work started on **26 October 2026**, straight after the kickoff call. The first commit in this repo is the start point; nothing here predates it.

- World state, safety layer (speed-and-separation, stop latch) and the orchestrator tool surface
- Adapter interfaces with fake implementations for development against recorded footage
- Scripts to run SAM 3.1 on the 8 GB laptop GPU: a memory and speed test, and a webcam preview that tracks a text prompt (`scripts/`)
- Mission control, calibration, model integrations (added as they land)

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

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m lookandpick.demo
```

Keys go in `.env` (see `.env.example`); it is git-ignored.

The SAM 3.1 scripts need Meta's code, installed separately outside this repo, and a Hugging Face login with access to the gated weights; see `PROGRESS.md` for the working setup and its workarounds.
