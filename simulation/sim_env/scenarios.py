"""Seeded, simulated-clock acceptance scenarios. No renderer or network required.

python run_scenarios.py --repeat 10 --output reports/acceptance.json
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
import numpy as np
from lookandpick.hand_safety import HandSafetyMonitor
from lookandpick.safety import point_segment_distance
from lookandpick.world import Hand
from .engine import Simulation


@dataclass(frozen=True)
class Scenario:
    name: str
    fault: str | None = None


SCENARIOS = [Scenario('clean_pick_place'), Scenario('hand_crosses_path'),
             Scenario('approach_withdraw'), Scenario('stop_mid_move'),
             Scenario('detector_dies', 'detector_stall'), Scenario('two_hands'),
             Scenario('object_occluded'), Scenario('unreachable'),
             Scenario('command_refused', 'command_refused'), Scenario('serial_dropout', 'serial_dropout')]


def run(scenario: Scenario, seed=0):
    sim = Simulation(realtime=False)
    sim.randomise(seed)
    base_step = sim.step
    samples = []
    event_time = None
    stop_time = None
    slow_onset = None
    detector_stamp = -1
    monitor = None
    if scenario.fault == 'detector_stall':
        monitor = HandSafetyMonitor(sim.world, sim.safety, sim,
                                    lambda:(sim.position,(.20,.08,.15)), clock=lambda:float(sim.data.time))
    def scripted_step():
        nonlocal event_time,stop_time,slow_onset,detector_stamp
        t = float(sim.data.time)
        if scenario.name in ('hand_crosses_path','two_hands') and t >= .15 and event_time is None:
            event_time = t
            p = sim.position
            sim.set_hand((p[0],p[1],p[2]+.03))
            if scenario.name == 'two_hands':
                sim.hands_list.append(Hand('second-hand',(.3,.3,.2),.045))
        if scenario.name == 'approach_withdraw':
            p = sim.position
            if .1 <= t < .5:
                offset = .55 - (t-.1)*1.2
                sim.hands_list = [Hand('approaching',(p[0],p[1]+offset,p[2]),.045)]
            elif t >= .5:
                sim.hands_list = []
        if scenario.name == 'stop_mid_move' and t >= .15 and event_time is None:
            event_time = t
            sim.halt()
        if scenario.fault == 'serial_dropout' and t >= .15 and event_time is None:
            event_time = t
            sim.faults.add('serial_dropout')
        if monitor:
            if t <= .15 and t > detector_stamp + .03:
                monitor.publish([],t)
                detector_stamp = t
            elif t > .15 and event_time is None:
                event_time = t
            monitor.tick()
        base_step()
        separation = min((max(0,point_segment_distance(h.position,tuple(a),tuple(b))-h.radius_m-.025)
                          for h in sim.hands_list for a,b in sim._segments(sim.data)),default=None)
        if sim.effective_scale < 1 and slow_onset is None and separation is not None:
            slow_onset = separation
        if event_time is not None and stop_time is None and sim.effective_scale == 0:
            stop_time = float(sim.data.time)
        samples.append({'time':round(float(sim.data.time),6),'scale':sim.effective_scale,'separation':separation})
    sim.step = scripted_step
    start = time.perf_counter()
    pick_success = None
    try:
        if scenario.name in ('clean_pick_place','object_occluded'):
            if scenario.name == 'object_occluded':
                class OccludedTracker:
                    def objects(self):
                        return [o for o in sim.objects() if not o.held]
                    def hands(self):
                        return sim.hands()
                sim.tools.tracker = OccludedTracker()
            picked = sim.tools.pick('obj1')
            placed = sim.tools.place('obj1',(.18,-.2,.018))
            pick_success = picked.startswith('picked') and placed.startswith('placed')
            passed = pick_success and sim.held is None
        elif scenario.name == 'unreachable':
            passed = sim.move_to((4,4,4),1) is False
        else:
            if scenario.fault == 'command_refused':
                sim.faults.add('command_refused')
            # Keeping a hand in the path causes a pause. End the scenario after 1s,
            # rather than waiting for a 20s wall-clock command timeout.
            if scenario.name in ('hand_crosses_path','two_hands'):
                original = sim.step
                def bounded():
                    original()
                    if sim.data.time >= 1:
                        sim.halt()
                sim.step = bounded
            result = sim.move_to((.20,.08,.15),1)
            if scenario.name == 'approach_withdraw':
                passed = result and any(s['scale'] < 1 for s in samples)
            elif scenario.name == 'command_refused':
                passed = not result and not samples
            else:
                passed = not result and stop_time is not None
        minimum = min((s['separation'] for s in samples if s['separation'] is not None),default=None)
        return {'scenario':scenario.name,'seed':seed,'passed':bool(passed), 'fault':scenario.fault,
                'sim_duration_s':round(float(sim.data.time),6),
                'wall_duration_s':round(time.perf_counter()-start,4),
                'minimum_separation_m':minimum,'slow_onset_distance_m':slow_onset,
                'hand_to_zero_ms':None if scenario.name not in ('hand_crosses_path','two_hands') or stop_time is None else round((stop_time-event_time)*1000,3),
                'stop_latency_ms':None if scenario.name != 'stop_mid_move' or stop_time is None else round((stop_time-event_time)*1000,3),
                'fault_to_zero_ms':None if scenario.fault is None or stop_time is None or event_time is None else round((stop_time-event_time)*1000,3),
                'pick_success':pick_success,'end_state':sim.state(), 'samples':samples}
    finally:
        sim.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat',type=int,default=10)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--output',type=Path,default=Path('reports/acceptance.json'))
    args=parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error('repeat must be in [1,100]')
    results=[run(s,args.seed+i) for s in SCENARIOS for i in range(args.repeat)]
    report={'simulation_only':True,'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print('| Scenario | Passed |')
    print('| --- | --- |')
    for s in SCENARIOS:
        selected=[r for r in results if r['scenario']==s.name]
        print(f"| {s.name} | {sum(r['passed'] for r in selected)}/{len(selected)} |")
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)
