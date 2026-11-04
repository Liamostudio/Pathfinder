# Motion task list (4 Nov 2026, for Claude Code)

Put this file in `docs/` in the Pathfinder repo. Work the tasks in order; commit each one on its own with tests and a PROGRESS entry. Hard stop at 18:00 UK: whatever works then is what gets filmed. Cut order if short of time: task 5, then task 4, then task 3's grip retries. Tasks 1 and 2 are never cut.

Another agent is adding Muse Spark Open in parallel. It edits `models/meta.py`, `glasses.py` and only the `--model` flag in `runtime.py` and `mission_control.py`. Don't edit those parts; pull before every commit.

## The behaviour we want

Once given an instruction, the arm keeps working at it until it's done or genuinely can't, like a colleague would. It waits when a hand is in the way, looks for a way around, recovers from faults on its own, finds the object again if it moves, and retries a missed grip. It only gives up for a reason that waiting or trying again won't fix, or when a person says stop or cancel, and then it says why in one sentence and what would help ("I can't reach the blue cube from here; move it closer and I'll try again"), and folds back to its Rest pose.

Spoken updates are short, local phrases (no model call, so no delay): "Pausing: a hand is in the way", "Going around", "Clear, carrying on", "Still waiting for the way to clear", "Lost sight of the red cube, looking again", "Heading back to rest".

## What the code does today (checked at `a8a0296`)

- **Mid-move, it already resumes.** The hand watchdog sets the arm's speed to zero when a hand is in the path and back up when it clears, so a paused move carries on by itself.
- **At the start of a move segment, it gives up.** `Tools._move` returns False if the speed is zero at that moment, so the whole pick or place fails ("blocked") instead of waiting.
- **Long pauses fail.** The simulator's move times out after 20 s even while paused, and the client halts and latches the arm after 30 s ("stop latched; reset required").
- **Every latch needs a manual reset**, whatever caused it: STOP, the stop word, a camera stall, detector loss or the simulator timeout.
- **No Rest pose in the simulator.** The real arm's Rest reading exists (`REST_READING` in `adapters/so101_profile.py`); the simulator's `reset` resets the whole scene instead of moving the arm.
- **No routing.** Moves are straight lines (hover, down, grip, up). A path through a no-go cube is refused; there is no planner (cuRobo was never connected). Live detections (SAM people, a mug) never affect motion; only hands (speed) and manual cubes (refusal) do.
- **Safety distances:** stop at 15 cm, slow inside 50 cm (`safety.py`), both placeholders.

## Rules that hold for every task

- A person's STOP (button, space bar, spoken "stop") is never cleared automatically. "Cancel" or "never mind" ends the current instruction without latching.
- **Resets end at Rest.** The Deck reset button, "cancel", and an instruction it couldn't finish all send the arm to its Rest pose. Recovering from a fault and "carry on" don't: they pick the instruction up where it stopped. If it's holding an object, it first puts it down where it picked it up (if that's clear); otherwise it goes to Rest holding it and says so.
- **The move to Rest is checked like any other** (hand check, no-go cubes), so it can pause and carry on too. It never runs while a person's STOP is latched.
- The fast hand check runs on every segment of every route, original or detour.
- **Stop distance becomes 10 cm** (from 15) for the simulated arm, with slow still inside 50 cm, so a route 10 cm from a hand is allowed and isn't immediately stopped by the hand check. The real-arm profile keeps 15 cm until it's measured on the bench.
- No change to the real-arm adapter; all of this is for the simulated arm, which is what the demo uses. (When the real arm gets Rest later: its Rest sits on the calibrated end stops of shoulder_lift and elbow_flex, so its version should stop a few degrees short.)
- Each task is committed on its own with tests and a PROGRESS entry, so stopping after any one of them leaves a working system.

## Task 1: keep waiting instead of giving up (never cut)

- [ ] In `Tools._move`: when the speed is zero only because of hands (not stopped, no invalid input, no zone violation), wait for the path to clear, re-checking every 50 ms. Say "Pausing: a hand is in the way" once, "Still waiting for the way to clear" every 20 s, and "Clear, carrying on" when it resumes. Then proceed through the same checks.
- [ ] No time limit on waiting: it waits until the path clears, a person says stop or cancel, or a fault stop happens (handled by task 2).
- [ ] Simulator (`engine._move_joints`): time spent at zero speed doesn't count towards the move timeout; raise the client's `command_timeout` to match, so a pause never triggers `halt()`. A move that makes no progress while it's allowed to move still times out (that's a real fault).
- [ ] Add the cancel words ("cancel", "never mind"), caught locally like the stop word: the current instruction ends, nothing latches, and the arm goes to Rest (task 2 adds Rest; until then it holds where it is).
- [ ] Set the simulated arm's stop distance to 10 cm (see the rules above).
- [ ] Tests with the real watchdog and threads: a hand in the path at segment start, then removed, means the pick completes; a hand that stays for two minutes means it is still waiting, with the reminders; "cancel" during the wait ends it without a latch; STOP during the wait stops at once.

## Task 2: automatic reset after a fault, and Rest (never cut)

- [ ] **Rest pose.** Map `REST_READING` to the simulator's joints and add `rest()` to the simulator and fake arm: a joint-space move to Rest at the speed the hand check allows. Add `Tools.go_rest()`, which puts down any held object first (as in the rules), checks the path, says "Heading back to rest", and can pause and carry on like any move.
- [ ] Split the latch reasons into **person stops** (STOP button, space bar, spoken "stop") and **fault stops** (camera stall, detector loss, a move that makes no progress). Record the reason with the latch.
- [ ] **Fault stops clear themselves.** A recovery supervisor (runtime-level, beside the gate) does what `Runtime.reset()` already does, building a fresh gate and monitor, once every camera has delivered fresh frames, the detector is healthy and the path has been clear for 2 s. Then the interrupted pick or place carries on from where it stopped (the waiting `_move` from task 1 picks it up). No Rest here.
- [ ] **Flapping guard:** at most 3 automatic resets a minute; after that it stays latched and says so ("A camera keeps dropping out; check the cable and say 'carry on'").
- [ ] **Person stops** stay latched, but can be cleared by saying "carry on" or "resume" (caught locally, like the stop word; no model) as well as by the Deck button. "Carry on" resumes the interrupted instruction where it stopped; "cancel" drops it and goes to Rest; the Deck reset button clears the latch and goes to Rest.
- [ ] `Runtime.reset()` (the Deck button) ends with `go_rest()` once the fresh gate is running.
- [ ] Deck shows which kind of stop it is and a countdown while it waits to recover.
- [ ] Tests: a camera stall latches, frames return, and it recovers after 2 s clear, then the pick completes without going to Rest; a stall with a hand still in the path doesn't recover until the hand leaves; four stalls in a minute stay latched; STOP never clears by itself; "carry on" clears a STOP and the pick resumes; the Deck reset ends at Rest; "cancel" while holding the cube puts it down, then Rest; a hand in the way on the way to Rest pauses, then carries on.

## Task 3: keep going when the first attempt doesn't work

- [ ] **Re-locate before each segment.** Refresh the target's position from the tracker before the approach and before the final descent; if it has moved more than 1 cm, re-aim. If it has left the view, say "Lost sight of the red cube, looking again", re-run SAM for up to 10 s, then carry on or explain.
- [ ] **Retry a missed grip.** After closing, check the object is held (the sim reports it; for vision, the object rose with the gripper). If not, open, back off, re-look, and try again, up to 3 attempts in total, then explain.
- [ ] **Orchestrator persistence** (`models/orchestrator.py` prompt): when a tool returns a reason, Spark should try a sensible alternative within the user's instruction (re-look, a nearby free drop spot, a different order of steps) rather than stop; ask the user one short question only when the instruction is genuinely ambiguous; and finish with one sentence saying what happened. It must not invent new goals or move anything the user didn't ask about.
- [ ] **Give up only for reasons that won't change by waiting:** the object can't be found after re-looking; the target is out of reach; every route is blocked by no-go cubes (they're fixed, so waiting won't help); three missed grips; a person's stop or cancel; the flapping guard. Each ends with one sentence on why and what would help, then `go_rest()` (except a person's STOP, which stays where it is until cleared).
- [ ] Tests: the target is nudged 3 cm during the approach and is still picked; a first grip that misses is retried and succeeds; an object that vanishes gives "can't find it" after the look-again window, then Rest; an unreachable drop gives a clear explanation, puts the cube back and goes to Rest; the orchestrator, given a blocked drop spot, chooses a free spot next to it.

## Task 4: route around no-go cubes

- [ ] When `zones.violation(start, end)` fires, try waypoint routes before refusing: (a) **up and over**: rise above the start to the lowest height that clears the blocked cubes plus the tool radius and a margin, cross, then descend; (b) **sideways**: one or two waypoints to the left or right of the blocked region. Take the shortest route where every segment passes `zones.violation()` and the arm's reach (IK) check.
- [ ] Each segment still goes through `_move`'s hand check, so a detour can also pause and resume. The move to Rest uses the same routing.
- [ ] Say "Going around" and tell the model ("rerouted over a no-go zone"); if nothing works, explain as in task 3.
- [ ] Show the waypoints in Deck's 3D view if it's cheap; otherwise the timeline is enough.
- [ ] Tests with real grids: a wall of cubes between the pick and the drop means over; a tall but narrow wall means around; boxed in means an explanation; every segment is checked.

## Task 5: reconsider a route around when a hand blocks the path

- [ ] While waiting in task 1 (at segment start, or paused mid-move for more than 1.5 s), look for a route around every 0.5 s: the same candidates as task 4, also keeping every segment at least **10 cm** from every current hand, and passing the no-go cubes and reach checks.
- [ ] Mid-move, this needs a way to end the current move **without** latching: add `cancel()` to the simulator and fake arm (the current target is dropped and the arm holds where it is), then start the new route from there.
- [ ] If a route is found, say "Going around", take it at the speed the hand check allows, and re-check every segment as normal. If the hand clears first, carry on the original path. If nothing is found, keep waiting (task 1).
- [ ] Tests: a hand parked on one side of the path with room on the other means a route around; a hand in the middle of a narrow workspace means a wait, then carry on; a hand that moves into the new route pauses it as normal; STOP during a reroute stops at once.
- [ ] Not today: obstacles the cameras detect (SAM people, a mug) as routing inputs. An RGB camera can't certify empty space, and the target and held object would need masking out.

## Demo shots this unlocks

- Hand reaches into the path: pause, "Going around" or "Clear, carrying on", pick completes.
- Nudge the cube mid-approach: it re-aims and still picks it.
- Unplug a camera mid-move: fault stop, plug it back in, it recovers and finishes by itself.
- Mug with no-go cubes drawn over it: "put the red cube on the other side of the mug", and the arm lifts over it.
- Say "cancel" mid-task: it puts the cube down and folds back to Rest.
- Ask for something impossible ("put it on the shelf" out of reach): one sentence on why and what would help, then Rest.

Log each task in PROGRESS with what was tested, and say in the README and video that fixed obstacles are marked by the user, not detected.
