# Pathfinder submission replay

Pre-recorded, labelled MuJoCo simulation. No Python backend, model key, CDN, hardware access or network is required to play it. Open `index.html` directly from the folder, or serve this entire folder on any static host. For a local HTTP check: `python3 -m http.server 8788 --directory replay`.

`demo.json` is one exported simulator run with trajectory frames, hand-gate states and Runtime/CallLog events. `demo-data.js` embeds that same JSON for browsers which block `file://` fetches. Open another exported JSON with the file chooser. Playback uses a schematic; it does not claim mesh-accurate robot geometry. All model actions in the seeded fixture are scripted and cost nothing.

Regenerate: `simulation/.venv/bin/python -m lookandpick.deck_demo --output replay/demo.json --embed`. The exporter injects an approaching/crossing hand during a real MuJoCo pick/place, clears the hand to resume, then triggers STOP during a second move. Simulation clock timing makes trajectory and event order repeatable; call latency placeholders are explicitly labelled. No physical safety milestone is established.
