# Hosted replay

The static player is `replay_site/`. It plays one exported Deck recording, with no backend, build step, framework, CDN, analytics or external asset. It works from a subdirectory on a static host. Open it through HTTP rather than `file://`, because browsers restrict fetching local JSON files.

The honesty banner is always visible above the player. The shipped **sample** is a recorded MuJoCo run from a separate Deck with `--backend sim --model fake`: injected hand stop → clear → pick (including a missed grip and retry) → place → scripted STOP. It is 14.9 seconds long, with 251 exported frames, 13 recorded events and zero API spend. No Muse, SAM, cameras or physical arm were used. Model latencies of zero are scripted placeholders; tool durations and event times come from the recorded Deck. Replace the take with the final approved simulation recording for submission.

## Export

From this worktree:

```bash
python3 scripts/export_replay.py recordings/launcher/takes/take-001.json
# Or read an isolated test Deck (never the live demo ports):
python3 scripts/export_replay.py --deck-url http://127.0.0.1:8782
# Optional destination, useful for reviewing an export before replacing the sample:
python3 scripts/export_replay.py /tmp/take.json --output /tmp/reviewed-take.json
```

The default output is `replay_site/data/take.json`. Input must have schema version 1, simulation mode, ordered non-negative frame times, finite numbers and valid marker-frame coordinates. Calls are sorted by their recorded timestamps. Frame spacing is at least 50 ms after rounding (at most 20 Hz); short transitions below that interval can be lost. Every input frame is validated, including frames omitted by thinning. All numeric JSON values are rounded to three decimals, including dollars: charges below $0.0005 round to zero, so the displayed sum is an approximate sum of rounded calls, not a billing ledger. A validation failure exits with `Export refused:` and leaves an existing output intact.

Only known display fields are exported. Unknown fields, camera metadata, images, tokens, credentials and file-path fields are discarded. Structured call inputs/outputs are recursively filtered. Recognisable keys, bearer credentials, private key headers, embedded URLs, image data, email addresses, international phone numbers, absolute file paths and long encoded strings in text cause refusal. Error messages identify the field without printing its contents. The loopback reader refuses redirects, proxies, credentials and ports below 8780; input is limited to 32 MiB.

This is a conservative heuristic filter, not a guarantee that arbitrary prose is anonymous. Review the exported title, captions, calls, labels and limitations before publication. Use a simulation take with non-personal commands. Do not add camera frames, pairing tokens, `.env` files or TLS keys to the static folder. The page only renders event text as text, never as HTML.

## Sample provenance / recording another fake take

The included JSON was fetched from `/api/replay` on a separate Deck on 8782. A non-rendering CPU MuJoCo server ran on 8781 (`simulation/run.py --port 8781 --no-render`); the Deck used `--backend sim --model fake --sim-url http://127.0.0.1:8781 --port 8782 --workspace-data /tmp/pathfinder-replay-workspaces`. Use the existing Python environments that contain each service’s dependencies. No GPU renderer or camera option is required.

On **that isolated Deck only**, reset, select the synthetic near-hand scenario, submit `pick up the red cube and put it next to the blue cube`, clear the synthetic hand after about 1.5 seconds, poll `/api/state` until the command finishes, request STOP, then export `/api/replay`. Retain explicit limitations identifying fake models and injected hand positions. Timing can vary; this sample is a captured run, not a promise of deterministic motion.

## Preview and checks

```bash
python3 -m http.server 8780 -d replay_site
# Open localhost:8780 in a browser.
python3 -m unittest discover -s tests -p test_export_replay.py -v
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/replay_site/*.test.mjs
```

Play/pause, drag or keyboard-scrub the slider, select 0.5×/1×/2×, or select a timeline event to pause at its timestamp. The highlighted call is the **nearest** event and may lie slightly ahead of the playhead; captions use only events already reached. Scrubbing backwards recomputes captions. Playback holds each recorded frame without inventing motion between samples. A hidden browser tab suspends elapsed-time accumulation.

The 2D arm is a schematic derived from `arm_position`; it is not joint forward kinematics or physical collision geometry. Objects marked held ride with the gripper. Translucent hand discs, a marker origin, metre grid (10 cm cells) and optional route waypoints share the marker frame (x right, y up). Routes are drawn from the current gripper through the recorded waypoints; they are display evidence only. `render(frame)` in `player.mjs` is the replacement boundary for a future 3D renderer. The exported joint positions are retained for that renderer.

`stop.kind` distinguishes person and fault stops. Only a fault with a recorded countdown displays recovery seconds and resets left. A hand-gate stop shows the hand warning even without a stop latch. A stopped take without a classified reason is labelled STOP LATCHED. The gauge reads the recorded speed scale and hand separation; no local playback control commands a robot.

The default sample has a hand stop and a person STOP, but no fault-recovery or reroute episode. Exporter tests cover those fields using fabricated data. Browser checks and the sample do not constitute real hand detection, paid model, camera-fault or hardware acceptance. The site fetches only local files; to verify internet independence, serve the folder locally and disconnect the network while playing. Do not switch off connectivity needed by the live demo just to perform this check.

## Static hosting — Liam’s publishing step

Publish **only the contents of `replay_site/`**, retaining `data/take.json` and the relative file layout. There are no environment variables or server endpoints to configure.

- **GitHub Pages:** use a Pages deployment workflow whose uploaded Pages artifact is the `replay_site` directory, then deploy that artifact with Pages. The normal branch-source UI cannot select an arbitrary `replay_site/` folder (it offers root or `docs/`); alternatively publish the contents as the root of a dedicated Pages branch. Configure publication in the chosen repository only after reviewing the take. No workflow or publishing branch has been created by this task.
- **Netlify drop:** after review, drag the `replay_site` folder into the manual deployment interface. There is no build command. Check the returned preview before using its URL in the submission.

Verify the published URL, banner, take limitations, scrubbing, console and asset loading. Avoid publishing the repository root, raw recordings or local service configuration. No deployment was performed by this task; publication and promotion are Liam’s decision.
