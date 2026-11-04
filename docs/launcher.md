# Pathfinder demo launcher

Start once from the repository root:

```sh
./scripts/pathfinder_launch.sh
```

It opens **http://127.0.0.1:8760/**. After that, use the page buttons. It reuses an
already-running launcher, refuses an unrelated listener, and writes its own
startup output to `recordings/launcher/launcher.log`. The launcher is separate
from Deck so it can stop and restart Deck. Keep the launcher tab open between
takes; **Stop all** stops its services while leaving the launcher available.

**Simulator only. The real arm must remain unpowered.** The launcher has no
serial-device or real-arm interface. Hardware checks read camera frames and
system status; they never enable torque or command motors.

## Click-through filming flow

1. **Hardware check:** runs on load. Re-check after re-seating USB cables. Camera
   names resolve to their capture nodes (`index=0`), never a remembered video
   index. The Ace Pro is probed at MJPG 640×360 and UGREEN at MJPG 1280×720, with
   a thumbnail and ArUco marker result. A probe closes the camera afterwards.
   The built-in Integrated Camera is excluded. A live launcher-owned Deck keeps
   its cameras during Re-check; the live status strip shows their freshness.
2. Tick **Real arm: 5V supply unplugged / kill switch in reach** and **Hands out of
   both camera views**. These are human confirmations, not electrical sensors.
   Both are required by the server before starting later stages.
3. Choose **Start setup / open Deck**, or **Use the existing workspace** as the
   equal fallback. Setup stops the relay, live Deck and SAM worker, then starts
   the simulator and a workspace-environment Deck without cameras/SAM. An
   existing reconstruction is reattached rather than abandoned. In Deck click
   **set up workspace**: import the glasses clip, sample about 1 fps, choose 2–4
   good views, reconstruct, align, draw the working volume/no-go cubes, save and
   activate. The launcher polls the workspace API and shows its stage/Ready
   version. The `#setup` link opens Deck; its existing navigation still requires
   the **set up workspace** click.
4. Choose **fake** for free rehearsal or **spark** (standard) for filming people.
   Model choices are read from the installed Deck's `--help` when the launcher
   starts; restart the launcher after another agent adds a model flag. Options
   apply on the next **Go live**. Turn **SAM people** off if the GPU is unavailable;
   the local CPU hand gate remains required. Turn **Second camera** off for an
   Ace-Pro-only gate; this does not select the UGREEN as a replacement primary.
5. **Go live** waits for reconstruction to finish/be cancelled, waits for the GPU
   lease, checks names/lenses/marker visibility, starts SAM if selected and waits
   for `loaded: true`. It restarts Deck in the hands environment, displays the
   hands-out calibration countdown and waits for >15 fps with fresh camera
   observations (both when enabled). The workspace opens after those checks.
   Startup failures include Deck's sanitised log, such as “marker seen in 0
   frames”. The countdown is an estimate; calibration/health determine readiness.
6. **Reset scene** between takes calls Deck reset first, waits for the simulator
   to be idle, unlatched and within 3 cm of its Rest TCP, then applies fixed prop
   positions. Only prop free joints change; the arm remains at Rest. The preset
   is refused if the real `zones.from_areas` guard finds a manual no-go overlap.
   The preflight also warns about the default red/blue cube positions. Edit the
   manual policy or the trusted config and retry; no cubes are silently cleared.
7. **Mark replay start** reserves a take number and records a UTC timestamp.
   **Mark replay end / save take** downloads Deck's replay, trims frames/events
   to this take, rebases their times and writes `takes/take-NNN.json`. It keeps
   the replay schema usable by the hosted player and adds start/end metadata.
   Start markers survive an interrupted export; numbers are not reused. These
   are simulator/schematic replays, not camera-video recordings. Self-check,
   reset and service changes are blocked during a take. Stop all attempts to
   export an interrupted take before stopping services.
8. **Self-check** stops Deck and SAM, runs the fixed `scripts/sim_motion_check.py`
   against the simulator, shows this run's PASS/FAIL output, then restores setup
   Deck on success. It takes about two minutes and repeatedly resets the scene.
   Choose Go live again afterwards to rebuild the camera gate. It is disabled
   while a take is recording. No model calls are needed by this check.
9. **Stop all:** cancels a pending stage and any verified workspace worker, then
   stops relay, Deck, helper jobs, SAM and simulator, waits and escalates to
   SIGKILL for stubborn owned groups. It checks the GPU lease and ports and
   reports anything still running. An unrelated process is never killed merely
   because it occupies a port. The logs folder button opens the fixed local
   directory with `xdg-open`; the sanitised log viewer works without a file manager.

If the browser blocks the automatic Deck tab, use the **Open Deck**, **Open
workspace setup** or **Open arm workspace** links on the page.

## Services and environments

| Service | Port | Interpreter / readiness |
| --- | --- | --- |
| Launcher | 8760 | `workspace_splat/.venv/bin/python`; stdlib HTTP/supervisor |
| Simulator | 8766 | `simulation/.venv/bin/python`; `/api/state` |
| Setup Deck | 8765 | `workspace_splat/.venv/bin/python`; `--backend sim`, no cameras/SAM |
| Live Deck | 8765 | `~/src/hands-venv/bin/python`; camera hand gate and optional `--sam-people` |
| SAM 3.1 | 8767 | `~/src/sam3-venv/bin/python`; `/health` with `loaded: true` |
| Optional glasses relay | 8772 | workspace interpreter; HTTPS on the Wi-Fi IPv4 |

Definitions, timeouts and marker-frame presets are in
`lookandpick/launcher/config.py`. The default presets are red `[0.24, -0.12]`
and blue `[0.16, -0.20]` metres; they are always checked against the current
manual policy. The simulator rejects invalid/overlapping/out-of-table presets
atomically, as well as presets during an arm command, held object or stop.

The hands environment was checked for Deck imports, workspace-library access and
UGREEN lens loading without opening a device or GPU. It needs no SciPy for those
paths. Reconstruction remains in the existing isolated geometry/SAM/Gaussian
interpreters; the launcher does not install or merge environments. Full browser
reconstruction under live Deck remains an in-person acceptance item.

The shared GPU lock is checked with non-blocking `flock`. Old metadata in an
unlocked file is normal; a locked file is not deleted or forcibly unlocked.
Go live has bounded waits and does not compete with a reconstruction. Stop all
uses the workspace service's existing PID/start-identity checks to cancel workers.

## Glasses link

This stage is optional and collapsed. Re-check to refresh the Wi-Fi IPv4. Create
TLS once, then start the relay. Existing certificates are checked against the
current IP and never overwritten. An IP mismatch requires local review; the
launcher does not delete private keys or silently trust a new certificate.
Install only `recordings/glasses-gateway/tls/ca.crt` on the iPhone. The UI shows
only the path to `recordings/glasses-gateway/pairing-token`, never its contents.
The relay uses the existing HTTPS gateway; pairing/trusting the development CA
remain explicit phone steps. The iPhone companion is still uncompiled and
untested on glasses. The reliable alternative is a glasses photo plus a voice
clip; see [the demo guide](demo-2026-11-04.md).

## Ownership and local security

Requests have bounded JSON bodies (4 KiB). Loopback Host and same-origin checks
reject remote origins and DNS rebinding hosts. Paths, commands, interpreters,
ports and preset coordinates cannot be supplied through the API. All service
commands come from the fixed config and use argument lists and new process
sessions, never `shell=True` or process-name kill patterns.

The supervisor records PID, kernel start time, group and argument fingerprint
in `recordings/launcher/processes.json`. A restarted launcher reattaches only
matching identities. Stop buttons for pre-existing services require a verified
isolated group in this repository, a recognised simulator-only command and the
same identity seen at preflight. A shared terminal group or unknown process must
be stopped locally. Its PID is reported. Stopping one process never kills the
launcher's own process group.

Child output is scrubbed before writing per-service logs, including values split
across pipe chunks, environment/.env credentials and the private pairing token.
Known values remain scrubbed after rotation. API responses and replay exports
are scrubbed too. Keys are configured locally in `.env`, never entered into this
page. Camera thumbnails stay in the local page's check results. Nothing is
uploaded to a hosted service by the launcher itself.

The live strip shows gate state/fps, per-camera ages, SAM health, voxel source
ages and last/total Spark cost for timeline entries observed by this launcher.
That total is not the whole Meta account bill and may omit history outside the
retained timeline. Missing/stale display data cannot grant safety clearance.
The hand gate, no-go policy and stop latch remain local and unchanged.

## Validation and remaining acceptance

```sh
workspace_splat/.venv/bin/python -m pytest tests/test_launcher.py -q
simulation/.venv/bin/python -m pytest simulation/tests/test_scene_preset.py -q
scripts/test_all.sh
```

Launcher tests use temporary fake services/probes: no serial port, camera, GPU,
external network or paid model call. They cover stage/GPU handover, cancelled
startup, exit/health errors, stubborn child groups, reattachment, port conflicts,
Host/Origin/body checks, secret scrubbing, real-grid zone warnings, reset order,
take trimming, self-check log boundaries and TLS mismatch. The camera probe is
checked with a synthetic marker frame behind a fake capture device. MuJoCo and
the real local simulator HTTP client check that presets move only props.

Browser acceptance is exercised against disposable fake services and a temporary
workspace, including safety gating, Setup/Go live, take start/end, reset, Stop
all and phone-width layout. **Not claimed:** actual camera hot-plugging, a live
SAM startup, live gate calibration, real person/hand voxel evidence, real glasses,
physical arm alignment or a filmed take. Liam must run those with the real arm
unpowered before relying on this for tonight's recording. No milestone is ticked.
