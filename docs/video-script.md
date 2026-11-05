# Pathfinder: Live Safety System for Robotics with Natural Language Commands — three-minute film, 4 November 2026

Solo entrant: Liam. British English. Master edit: **3:00 exactly**; slots below total 180 seconds. Read only the quoted narration and live commands. Captions, evidence and production notes are not spoken. Allow model latency and arm motion to play beneath narration; keep cuts visible and label sped-up footage. No synthetic replies presented as live Meta calls.

Evidence key: all entries are in [PROGRESS.md](../PROGRESS.md). **E1:** 4 Nov evening “re-scan and robot placement”; **E2:** 2 Nov workspace setup/editor; **E3:** 4 Nov midday “second camera running behind the fused hand gate, live”; **E4:** 4 Nov afternoon “motion tasks 1 to 5”; **E5:** 4 Nov evening “SPOKEN commands…” (17:52–17:54); **E6:** 4 Nov simulator Rest/planner entries; **E7:** 3 Nov tip-touch/commissioned-profile entries and 30 Oct powered-joint tests; **E8:** 26–28 Oct safety/tools/model implementation entries. See [shot list](shot-list.md) for actions, retakes and fallbacks.

The default scan shot uses **phone photos**, because E1 says the glasses video decoded but its reconstruction was not run. Do not call it a scan made with the glasses. The default glasses shot uses named cubes, because real-photo “this one” selection remains unverified. The physical arm stays unpowered and static throughout.

## 1 · 0:00–0:15 · Shared table (15 s)

Narration:

> A robot sharing a table with someone needs to notice when they reach in. I'm Liam, and this is Pathfinder: a prototype safety layer, demonstrated tonight with a simulated arm.

Caption: **Pathfinder · Robots and people sharing a table**. Persistent badge on moving-arm shots: **SIMULATED SO-101 · MuJoCo**.

Models named: none. Evidence: E3, E7, E8. Physical arm beauty shot is static; do not imply it is moving in sync with the screen.

## 2 · 0:15–0:40 · Workspace and manual rules (25 s)

Narration:

> I start with photographs of the workspace. Meta's MapAnything estimates the geometry, and SAM three point one separates surfaces. I can edit the map and draw a no-go cube. That cube is a rule the arm must obey. Fixed obstacles are marked by me; they are not automatically detected.

Caption: **Phone photos → MapAnything + SAM 3.1 → editable workspace**; then **Manual no-go cube = motion restriction**.

Models named: **MapAnything**, **SAM 3.1** (local). Evidence: E1, E2, E4. Use the existing reconstructed workspace; show capture → result as an edit, not instant reconstruction. No claim of geometric accuracy.

## 3 · 0:40–1:10 · Live hand interruption (30 s)

Narration:

> Two cameras watch the table. Google's MediaPipe is the fast hand gate: it slows or stops the simulated arm. SAM three point one adds people and hands to the voxel map, but it is too slow to enforce safety. When my hand clears, a hand pause can continue. An explicit stop stays latched until I ask to resume.

Live command, after pressing STOP and removing the hand:

> Carry on.

Caption: **MediaPipe: fast local hand gate**; **SAM 3.1: slower map observations**; **150 ms watchdog · camera stall → latched stop**. For a plain hand pause: **Waiting for hand → clear → continuing**. For explicit STOP: **STOP latched → explicit Carry on**.

Models named: **MediaPipe Hand Landmarker**, **SAM 3.1**. Evidence: E3, E4, E6. Keep automatic hand-pause clearance distinct from explicit STOP/resume. A 150 ms watchdog deadline is not a measured physical stopping time. `[CHECK]` Capture this combined moving-arm/live-hand take tonight; E3 proves live detection and E4 simulator behaviour separately, not this edited take.

## 4 · 1:10–1:50 · Glasses command (40 s)

Live command, tap-to-speak into glasses:

> Pick up the red cube and put it next to the blue cube.

Narration, after the command; show transcript review and tap Confirm:

> The glasses microphone feeds the iPhone app. Muse Voice Transcribe turns speech into text, which I review. Muse Spark then plans through five constrained tools: get scene, pick, place, stop and say. Each action still passes the local checks. This route has completed a real glasses speech command to the simulator, with replies heard in the glasses.

Caption: **Meta glasses → iPhone → Muse Voice Transcribe 1.0 → review → Muse Spark 1.3**; **Five validated tools · local checks on actions**; small label **Object positions: simulator ground truth**.

Models named: **Muse Voice Transcribe 1.0**, **Muse Spark 1.3** (API). Evidence: E5, E8. E5 demonstrated separate spoken pick and place instructions; the combined wording has worked from laptop speech (4 Nov midday Voice entry). `[CHECK]` Confirm the combined glasses wording in the filmed take; otherwise use two commands verbatim: “Pick up the red cube.” / “Place the red cube next to the blue cube.” Both are evidenced by E5. Preserve review/Confirm in the edit; shorten waiting with a labelled cut. Do not script a fabricated “Done” from the model.

## 5 · 1:50–2:10 · No-go refusal (20 s)

Narration:

> If I block the target with a no-go cube, the request is refused. For a blocked path, the local planner can try over or around, checking each leg. It is a bounded search, not a guarantee of full-arm clearance.

Caption: **Target blocked → refusal**; **Path blocked → checked detour, or refusal**.

Models named: none; Spark may explain the result but does not override it. Evidence: E4, E6; demo guide warns the active manual cubes already cover the red target. Refusal is the required shot; successful rerouting is optional footage, never promised for every layout.

## 6 · 2:10–2:25 · Cancel (15 s)

Live command:

> Cancel.

Narration:

> Cancel ends the instruction. If the way is clear, it puts a held cube back and heads to Rest. A person's stop takes priority, and never clears itself just because a model replies.

Caption: **Cancel → safe put-back attempt → Rest**; **Person STOP has priority**.

Models named: none: local control. Evidence: E4, E6. Film in a clear simulated workspace with no active person STOP. If put-back or Rest is refused, keep that refusal and use the shorter alternative: “Cancel ends the instruction. Returning to Rest is checked too; if the way is blocked, it waits or refuses.”

## 7 · 2:25–2:45 · Architecture (20 s)

Narration:

> Models see and decide; the local layer enforces. Network calls are outside the safety path. The hand gate, watchdog, no-go rules and stop latch remain local. Models never drive the motors. I built this integration during the hackathon; the repository credits the upstream work.

Caption slide, made as text in the video editor:

```text
LOCAL VISION: MediaPipe hand gate / SAM 3.1 map / MapAnything scan
API INTENT: Muse Voice Transcribe 1.0 → Muse Spark 1.3
                         ↓ five validated tools
LOCAL ENFORCEMENT: hand gate · watchdog · manual zones · STOP
                         ↓ checked actions
                    MuJoCo SO-101
Models never drive the motors.
```

Models named on slide: all five above. Evidence: E2–E5, E8 and PROGRESS “Built during the hackathon”. Keep the SAM map/MapAnything scan visually outside the enforcement box.

## 8 · 2:45–3:00 · Honest close (15 s)

Narration:

> Tonight's arm is simulated. The real SO one oh one is built and tip-touch calibrated, with powered joint tests only. This is not certified safety. Next comes supervised validation, towards robots sharing our workspace.

Caption: **Real SO-101: built + calibrated · joint tests only**; **Next: supervised physical validation**; **github.com/Liamostudio/Pathfinder**.

Models named: none. Evidence: E7 and no later Cartesian acceptance in the 4 Nov log. Do not replace this with a physical pick/hand-stop claim unless a new PROGRESS entry supplies that evidence.

## Read-through and edit acceptance

The default quoted text is 363 words including live commands: about 2:45 at an average 132 words/minute, leaving 15 seconds for pauses and response sound. This is a pacing estimate, **not a measured read-aloud acceptance**. Liam should record one timed read, using the fixed shot slots to hold the final edit at 3:00; the close needs about 140 words/minute. Aim for a natural 2:50–3:05 spoken take including pauses, then trim silence/hold pictures to the 3:00 master. Do not speed up narration to conceal an overrun. `[CHECK]` Log the actual read-through duration before export.

All factual narration maps to the evidence above. `[CHECK]` marks pending filming/read-through acceptance, not permission to assert an unverified capability. If footage is replayed, scripted or synthetic, label it in frame for its entire use; use the fallback narration in the shot list. Check captions remain readable at phone size and the final frame ends at 3:00.
