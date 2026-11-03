"""Deterministic MuJoCo filming demo and static replay exporter. No hardware or API calls.

simulation/.venv/bin/python -m lookandpick.deck_demo --output replay/demo.json
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from .deck_runtime import ScriptedModel, simulated_gate
from .models.calllog import CallLog
from .runtime import Runtime

class DemoLog(CallLog):
    def __init__(self,clock):super().__init__(keep=1000);self.clock=clock;self.records=[]
    def record(self,source,input,output,latency_ms,**kwargs):
        entry=super().record(source,input,output,0.,**kwargs);t=round(self.clock(),6)
        entry.update(t=t,time=datetime.fromtimestamp(t,timezone.utc).isoformat(),scripted=True)
        self.records.append(entry);return entry
    def entries(self):return [dict(e) for e in self.records]


def run_demo(seed=0):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'simulation'))
    from sim_env.engine import Simulation
    sim=Simulation(realtime=False);sim.randomise(seed)
    start=float(sim.data.time);clock=lambda:float(sim.data.time)-start
    log=DemoLog(clock);runtime=Runtime(ScriptedModel(log),arm=sim,log=log,say=lambda _:None)
    frames=[];step=sim.step;phase='pick';hand_stage=None;last_gate=None;stop_start=None;stop_sent=False
    def snapshot():
        state=sim.state();gate=simulated_gate(state);gate['updated']=round(clock(),6)
        return {'t':round(clock(),6),'scene':state['scene'],'hand_gate':gate,'stopped':state['stopped'],
                'joints':state['joints'],'speed_scale':state['speed_scale']}
    def scripted_step():
        nonlocal hand_stage,last_gate,stop_sent
        t=clock()
        if phase=='pick':
            stage='clear' if t<.15 or t>=.8 else 'slow' if t<.4 else 'stop'
            if stage!=hand_stage:
                hand_stage=stage
                log.record('Safety','scripted hand crossing',stage,0)
            p=sim.position
            sim.set_hand(None if stage=='clear' else (p[0],p[1]+(.4 if stage=='slow' else .03),p[2]))
        if phase=='stop' and clock()-stop_start>=.15 and not stop_sent:
            stop_sent=True;runtime.stop();log.record('Safety','STOP','Stop latched during motion; no model call',0,status='blocked')
        step()
        state=sim.state();scale=state['speed_scale'];gate='latched' if state['stopped'] else 'stop' if scale==0 else 'slow' if scale<1 else 'clear'
        if gate!=last_gate:last_gate=gate;log.record('Safety','hand gate',gate,0,status='blocked' if gate in ('stop','latched') else 'ok')
        if not frames or clock()-frames[-1]['t']>=.04-1e-9:frames.append(snapshot())
    sim.step=scripted_step
    try:
        log.record('system','seeded demo',f'MuJoCo seed {seed}; scripted model; simulation only',0)
        result=runtime.command('pick up the red cube and put it next to the blue cube')
        if result.error or not any(s['result'].startswith('placed') for s in result.steps):
            raise RuntimeError('Demo pick/place failed: '+json.dumps(result.steps))
        phase='stop';stop_start=clock()
        sim.move_to((.20,.08,.15),1)
        if not stop_sent:raise RuntimeError('Demo STOP did not interrupt a move')
        for _ in range(60):sim.step()
        frames.append(snapshot())
        return {'schema_version':1,'mode':'simulation replay','title':'Pathfinder / seeded pick, hand crossing and STOP',
                'seed':seed,'frames':frames,'events':log.entries(),
                'limitations':['Pre-recorded MuJoCo simulation; not live hardware.','Scripted model; no API request or charge.',
                               'Ideal grasp and kinematic servos. Hand positions are injected ground truth.',
                               'Call latencies are zero placeholders; trajectory and event times use the simulation clock.']}
    finally:sim.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('replay/demo.json'));p.add_argument('--seed',type=int,default=0);p.add_argument('--embed',action='store_true');a=p.parse_args()
    data=run_demo(a.seed);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(data,separators=(',',':'),allow_nan=False)+'\n')
    if a.embed:a.output.with_name('demo-data.js').write_text('window.PATHFINDER_DEMO = '+json.dumps(data,separators=(',',':'),allow_nan=False)+';\n')
    print(f'Exported {len(data["frames"])} frames, {len(data["events"])} events, {data["frames"][-1]["t"]:.2f} simulation seconds to {a.output}')
if __name__=='__main__':main()
