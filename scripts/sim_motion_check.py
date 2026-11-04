"""The motion behaviours (docs/motion-tasks-2026-11-04.md) against the REAL MuJoCo simulator, not fakes. Needs the bridge running on
127.0.0.1:8766 (simulation/.venv/bin/python simulation/run.py). It RESETS the scene between scenarios and sends nothing anywhere else.

  PYTHONPATH=. workspace_splat/.venv/bin/python scripts/sim_motion_check.py
"""
import json
import sys
import threading
import time

sys.path.insert(0, "simulation")
from sim_env.client import SimulationClient  # noqa: E402

from lookandpick.models.base import ChatResult, FakeLLM, ToolCall  # noqa: E402
from lookandpick.models.calllog import CallLog  # noqa: E402
from lookandpick.runtime import Runtime  # noqa: E402

client = SimulationClient()
OUT = []


def call(name, args):
    return ToolCall(name, name, json.dumps(args))


def fresh(script):
    client.action("reset")
    said = []
    rt = Runtime(FakeLLM(script), client, log=CallLog(), say=said.append)
    rt.tools.look_s = 3.0
    return rt, said


def pos(name):
    return next(o["position"] for o in client.state()["scene"]["objects"] if o["label"] == name)


def run_thread(rt, text):
    out = []
    t = threading.Thread(target=lambda: out.append(rt.command(text)), daemon=True)
    t.start()
    return t, out


def report(name, ok, detail=""):
    OUT.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + detail if detail else ""), flush=True)


PICK = lambda: [ChatResult("", (call("pick", {"id": "obj1"}),)), ChatResult("Done.")]

# 1. plain pick and place still works, and a rest afterwards
rt, said = fresh([ChatResult("", (call("pick", {"id": "obj1"}), call("place", {"id": "obj1", "loc": [0.18, -0.2, 0.018]}))), ChatResult("Done.")])
r = rt.command("pick up the red cube and put it there")
report("pick and place", [s["result"] for s in r.steps] == ["picked red cube", "placed red cube"], str([s["result"] for s in r.steps]))
t0 = time.time()
rest = rt.tools.go_rest()
st = client.state()
report("go_rest on the real simulator", rest == "at rest" and abs(st["scene"]["arm_position"][0] - st["rest_position"][0]) < 0.02, f"{rest} {time.time()-t0:.1f}s arm={[round(x,3) for x in st['scene']['arm_position']]} rest={[round(x,3) for x in st['rest_position']]} said={said[-2:]}")

# 2. grip miss once -> retried
rt, said = fresh(PICK())
client.grip_miss_once()
r = rt.command("pick up the red cube")
report("first grip misses, retried", r.steps[0]["result"] == "picked red cube" and "Missed it, trying again" in said, f"{r.steps[0]['result']} said={said}")

# 3. nudged 3 cm mid approach
rt, said = fresh(PICK())
t, out = run_thread(rt, "pick up the red cube")
time.sleep(0.6)
client.nudge("obj1", 0.03, 0.0)
t.join(60)
report("target nudged 3 cm mid-approach is still picked", bool(out) and out[0].steps[0]["result"] == "picked red cube", f"{out[0].steps[0]['result'] if out else 'no result'} cube={[round(x,3) for x in pos('red cube')]} said={said}")

# 4. object vanishes
rt, said = fresh(PICK())
rt.tools.look_s = 2.0
t, out = run_thread(rt, "pick up the red cube")
time.sleep(0.6)
client.hide("obj1", True)
t.join(60)
st = client.state()
report("vanished object: cannot find, then Rest", bool(out) and out[0].steps[0]["result"].startswith("blocked: cannot find") and
       abs(st["scene"]["arm_position"][0] - st["rest_position"][0]) < 0.03, f"{out[0].steps[0]['result'] if out else 'none'} said={said} arm={[round(x,3) for x in st['scene']['arm_position']]}")
client.hide("obj1", False)

# 5. hand in the way at the start: waits, then carries on
rt, said = fresh(PICK())
tgt = pos("red cube")
client.action("hand", position=[tgt[0], tgt[1], 0.05])
t, out = run_thread(rt, "pick up the red cube")
time.sleep(1.5)
waiting = t.is_alive() and client.state()["held"] is None
client.action("hand", position=None)
t.join(60)
report("hand on the target: waits, then carries on", waiting and bool(out) and out[0].steps[0]["result"] == "picked red cube" and "Pausing: a hand is in the way" in said, f"waiting={waiting} said={said}")

# 6. hand parked beside the arm mid-carry: a route is found and announced; the simulator's body-link check may still hold it, never a latch
rt, said = fresh([ChatResult("x")])
rt.tools.pick("obj1")
client.action("hand", position=[0.19, -0.075, 0.08])
out2 = []
t2 = threading.Thread(target=lambda: out2.append(rt.tools.place("obj1", (0.15, -0.25, 0.018))), daemon=True)
t2.start()
for _ in range(100):
    if "Going around" in said:
        break
    time.sleep(0.1)
time.sleep(2.0)
no_latch = not client.state()["stopped"] and t2.is_alive()
client.action("hand", position=None)
t2.join(90)
res = out2[0] if out2 else "none"
report("hand mid-carry: announces a route, never latches, places once clear", "Going around" in said and no_latch and res.startswith("placed red cube"), f"{res} said={said}")

# 7. cancel while holding: cube put back, then Rest
rt, said = fresh([ChatResult("", (call("pick", {"id": "obj1"}), call("place", {"id": "obj1", "loc": [0.18, -0.2, 0.018]}))), ChatResult("Done.")])
start = list(pos("red cube"))
t, out = run_thread(rt, "pick up the red cube and put it there")
for _ in range(200):
    if client.state()["held"] == "obj1":
        break
    time.sleep(0.1)
time.sleep(0.5)
rt.command("cancel")
t.join(90)
st = client.state()
back = pos("red cube")
report("cancel while holding: put back, then Rest", st["held"] is None and abs(back[0] - start[0]) < 0.03 and abs(back[1] - start[1]) < 0.03 and
       abs(st["scene"]["arm_position"][0] - st["rest_position"][0]) < 0.03, f"start={[round(x,3) for x in start]} back={[round(x,3) for x in back]} said={said}")

# 8. STOP mid-pick then carry on
rt, said = fresh(PICK())
t, out = run_thread(rt, "pick up the red cube")
time.sleep(0.8)
rt.stop()
time.sleep(1.5)
held_up = t.is_alive() and client.state()["stopped"]
ok = rt.command("carry on").reply == "carrying on"
t.join(60)
report("STOP then 'carry on' resumes the pick", held_up and ok and bool(out) and out[0].steps[0]["result"] == "picked red cube", f"held_up={held_up} said={said}")

# 9. Deck-style reset ends at Rest
rt, said = fresh(PICK())
rt.stop()
rt.reset()
st = client.state()
report("reset after a stop ends at Rest", not st["stopped"] and abs(st["scene"]["arm_position"][0] - st["rest_position"][0]) < 0.03, f"arm={[round(x,3) for x in st['scene']['arm_position']]} said={said}")

print()
print(f"{sum(1 for _, ok in OUT if ok)}/{len(OUT)} passed")
