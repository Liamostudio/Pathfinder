import assert from 'node:assert/strict';
import {test} from 'node:test';
import {QUALITY_PRESETS,estimateBuild,remainingTime,duration} from '../web/quality.js';

test('higher detail and larger input sets increase estimated work',()=>{
 const quick=estimateBuild(QUALITY_PRESETS[1000],40),ultra=estimateBuild(QUALITY_PRESETS[20000],40);
 assert.ok(ultra.low>quick.low);assert.ok(ultra.high>quick.high);
 assert.ok(estimateBuild(QUALITY_PRESETS[20000],500).high>ultra.high);
 assert.ok(ultra.low<ultra.high);
});
test('local fit measurements calibrate future estimates',()=>{
 const scene=seconds=>({trained:true,training:{max_edge:640,max_gaussians:40000,history:[{elapsed_s:seconds,step:2500}]}});
 const fast=estimateBuild(QUALITY_PRESETS[20000],4,[scene(10)]),slow=estimateBuild(QUALITY_PRESETS[20000],4,[scene(40)]);
 assert.ok(fast.calibrated&&slow.calibrated);assert.ok(slow.low>fast.low);
});
test('remaining time waits for warm-up and decreases with progress',()=>{
 const entry={stage:'training',running:true,steps:1000,step:100,elapsed_s:10};
 assert.equal(remainingTime({...entry,step:1}),null);
 assert.ok(remainingTime({...entry,step:500,elapsed_s:50}).high<remainingTime(entry).high);
 assert.equal(remainingTime({...entry,stage:'complete',running:false}),null);
 assert.equal(duration(90),'2 min');assert.equal(duration(3660),'1 hr 1 min');
});
