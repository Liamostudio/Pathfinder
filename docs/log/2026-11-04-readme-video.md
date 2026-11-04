# README, licences and video — 4 November 2026

Followed `docs/codex-brief-readme-video-2026-11-04.md`; read CLAUDE.md first, then the demo/model notes and dated progress evidence. Worked in **`../pathfinder-docs`**, branch **`readme-video`**, created from main at `541b41e`. README was the first authored deliverable. Own files only: `README.md`, `docs/licences.md`, `docs/video-script.md`, `docs/shot-list.md`, this log. No edits to PROGRESS.md; Claude can merge this entry.

## Delivered

- README restructured in the requested order, with evidence table, simulator/physical split, event-built/upstream attribution, named Meta tooling, no-keys simulator setup, safety rules, honest limits and missing original-code licence flag.
- Licence inventory covers named models, direct manifests, known imported/support libraries, optional developer paths, desktop bindings and bundled fonts/robot assets. Upstream sources linked per row; failed/exact-build checks explicitly **unverified**. No guessed permissive grants. No model weights committed.
- Eight-beat video script and matching solo shot list: **15 + 25 + 30 + 40 + 20 + 15 + 20 + 15 = 180 seconds**. Default narration/commands: **363 whitespace-delimited words**. Expected speech about 2:45 at 132 words/minute, with 15 seconds for pauses; actual read-through/filming acceptance remains `[CHECK]` for Liam. Every beat has captions, model names/evidence, concrete actions, a fallback and a retake note. No synthetic model replies scripted as real.

## Evidence decisions

Used the latest 4 Nov evening PROGRESS entries in the shared main checkout (concurrently committed as `f55a9fc` during this task), rather than stale earlier “not connected” summaries: real glasses speech through Muse Voice Transcribe/Spark to MuJoCo, feedback in glasses reported by Liam, missed-grip retry, and phone-photo reconstruction after reducing views. Glasses video decoded but reconstruction not run; real-photo “this one” remains unverified. Script therefore uses phone-photo scan footage and named cubes. Combined glasses pick/place wording and combined moving-arm/live-hand filming are marked `[CHECK]`, with evidenced separate-command and clearly labelled simulator fallbacks.

No new real-arm acceptance found: only powered joint tests and torque-off tip-touch calibration, approximately 2 mm held-out height error near touched poses. Physical arm is unpowered/static in the shot list. SAM is the slower advisory map feed, not the fast safety path; voxel unknown does not become free from RGB; only manual map restrictions hard-gate the current simulated moves. Distinguishes ordinary hand pause/clearance from explicit person STOP and fault recovery. Hand rerouting does not certify full-arm clearance.

## Licence findings for Liam

- No root licence for original Pathfinder code. A licence choice remains Liam's decision; none added without instruction.
- `plyfile` **1.1.5 is GPLv3** (pinned upstream COPYING checked). Optional `evo` also has GPLv3 upstream terms. Distribution/combination implications are recorded, not silently classified as Apache.
- MapAnything Apache-variant model card verified tonight. Exact cache revision licence correspondence was checked in the later 3 Nov integration baseline/provenance entry, resolving the earlier integration doc's concern; exact-revision web retrieval failed tonight, so this is not claimed as an independent new hash/cache audit.
- Pinned DA3-LARGE-1.1 upstream model card says **Apache 2.0**, conflicting with Splat Studio README's CC BY-NC statement. Recorded discrepancy; did not edit that out-of-scope README or assume all DA3 weights share one licence.
- Hand and Pose model cards say Apache 2.0. Exact EfficientDet-Lite0 float16 weight terms could not be verified from readable authoritative material; marked unverified rather than copying earlier Apache assertions. Remaining API/DAT exact-version, Feetech SDK, exact draccus and platform/codec gaps are explicit in the inventory.

## Validation and handover

Read exact mission-control flags, simulator launcher/requirements, full test script and environment guides; documentation commands were **not executed**. Checked local Markdown targets against repository file inventory, eight timing slots, quote-only narration word count, authorised file scope and Git whitespace checks. No application tests run: brief permits documentation/read-only Git work and prohibits services/devices/GPU access. No service started, no camera/GPU/arm/serial use, no port interaction, no paid API call, no secret inspected or added.

Liam still needs the actual read-aloud timing, filmed results for `[CHECK]` shots, licence selection and the explicitly unverified distribution terms. These are not represented as passed acceptance. Re-read PROGRESS immediately before filming for any new physical acceptance. Push only `readme-video`, after `git pull --rebase origin main`; never main. Worktree is retained for review.
