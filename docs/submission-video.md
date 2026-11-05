# The submission video: what it shows, and what each claim rests on

This records the video as submitted (captions exported 5 Nov 2026), not the earlier planned script. `docs/video-script.md` and `docs/shot-list.md` were written before the film and are **superseded** by this page where they differ. The most important difference: the planned film kept the real arm unpowered, whereas the submitted film's last section shows the real SO-101 stopping and restarting for a hand.

Evidence for every row below is in [`PROGRESS.md`](../PROGRESS.md) (the 4 Nov entries) and [`technical-breakdown.md`](technical-breakdown.md).

## The five sections (title slides, in order)

| Time | Slide | Models named on the slide | What is shown | Simulated or real |
| --- | --- | --- | --- | --- |
| 0:00 to 0:28 | **1: Mapping the Workspace** | MapAnything (Meta), SAM 3.1 (Meta), gsplat | Four phone photos (front-right, front-left, top-down, with a sharp marker) imported, geometry reconstructed into a 3D scene | Real photos, real local reconstruction |
| 0:29 to 0:59 | **2: Voxel Spatial Safety System** | SAM 3.1 (Meta), MediaPipe (Google) | Two cameras locate the hand; voxel cubes and no-go zones; the arm slows, stops or goes around | Real cameras and real hands; the arm shown is the **MuJoCo simulation** |
| 1:00 to 1:56 | **3: Natural Language Command** | Muse Voice Transcribe, Muse Spark, SAM 3.1 (all Meta) | Tap to speak on the iPhone, glasses microphone, transcript review, send to the simulator; "pick up the blue cube", "place the blue cube next to the red cube" | Real speech and Meta API calls; **simulated arm** |
| 1:57 to 2:35 | **3b: Chained Commands** | Muse Voice Transcribe, Muse Spark, SAM 3.1 (all Meta) | "Pick up the red cube and place it on the other side of the blue cube", one chained instruction | Real speech and API calls; **simulated arm** |
| 2:36 to 2:58 | **4: Realtime Safety Demo** | SAM 3.1 (Meta), MediaPipe (Google), LeRobot | Hand near the **real SO-101**: it stops, restarts when the hand leaves, stops, restarts | **Real arm** (a slow single-joint sweep, Liam at the bench with the 5V kill switch in reach) |

## Claim check (what is solid, what needs careful wording)

**Solid, backed by the ledger**
- Four-photo scan through MapAnything, SAM 3.1 masks and gsplat (2 and 4 Nov entries). The scan used phone photos, not glasses video.
- Two cameras fused behind one hand gate; the hand slows then stops the simulated arm; voxel cubes turn red for a person (4 Nov "second camera running behind the fused hand gate, live").
- Glasses, then iPhone, then Muse Voice Transcribe, transcript review, Muse Spark, five constrained tools, simulated arm (4 Nov "SPOKEN commands from the Meta glasses ..."). Chained commands work in the simulator (4 Nov motion tasks; the sim's pick/place chain).
- Section 4: the real arm stopped for a hand and restarted when the hand left (4 Nov evening "REAL SO-101 hand-stop demo"; Liam observed it, and the terminal showed the arm slowing, `frozen` at scale 0.00, then `clear: carrying on`).

**Wording to be careful with (these overstate what the evidence supports)**
1. **"Sam 3.1 gating ... it stops the robot arm" and "it uses Sam 3.1 hand and person tracking to make sure it's not going to hit."** The arm is stopped by the **MediaPipe hand gate** (150 ms watchdog). SAM 3.1 runs about once a second and only paints people and hands into the voxel map; it is deliberately **not** in the safety path. Slide 2 lists both models, which is right. For any written text, say: *MediaPipe stops the arm; SAM 3.1 marks people and hands in the voxel map.*
2. **SAM 3.1 on slides 3 and 3b.** In the live demo the objects came from the simulator's own state, not from SAM 3.1 finding them (the language-to-target path with Spark naming objects and SAM finding them was verified on simulator images only). SAM 3.1 is on those slides honestly only if you mean the voxel feed running in the background. Safer: describe the voice path as Voice Transcribe plus Muse Spark plus the five tools, and keep SAM 3.1 to the scan and the voxel map.
3. **SAM 3.1 on slide 4.** The real-arm script reads the camera hand positions published by the Deck's MediaPipe gate. SAM 3.1 was running in the Deck (voxel map) but is not in the real arm's stop path. LeRobot is correct (it drives the real arm's bus).
4. **"Everything you saw before, this is it working on a real arm."** The real arm demonstrated one thing: the hand stop, on a slow single-joint sweep. It did not take voice commands, pick, place or route around anything. Suggested wording: *"This is the same live hand gate stopping and restarting the real arm."* This matters because the README is explicit that the voice and pick/place demos are on the simulated arm.
5. **"The arm will either move out of the way or stop."** True of the simulator: it goes round only when the hand is clear of the whole arm; otherwise it holds until the hand leaves. The real arm only freezes and resumes.
6. **Caption text.** The auto-captions contain recognition errors ("Psam 3.one" for SAM 3.1, "Vauxhall system" for voxel system, "metaspark" for Muse Spark, "red view" for red cube, a stray "magically"). Fix these before uploading subtitles.

## What the real-arm section does not claim (and the repo says so)

- Not a pick or place, and not through the voice pipeline.
- The 10 cm stop distance is a chosen margin, not a measured safe distance; stopping latency is unmeasured; the safety layer is not certified.
- The real-arm hand check reads the Deck's gate over loopback HTTP and fails closed; it is not an in-process gate like the simulator's.
- The fold-back at the end of a run faulted on the bench and was fixed afterwards, but that fix has not been re-run on the arm.

See [`real-arm-hand-stop-bench.md`](real-arm-hand-stop-bench.md) for the full test notes.
