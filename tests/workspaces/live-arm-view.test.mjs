import assert from 'node:assert/strict';
import {LiveArmView,JointEase,freshArm,validJoints} from '../../lookandpick/web/live-arm-view.js';

let now=100,calls=0,holds=0;const updates=[],badges=[];
const manifest={robot:{placement_status:'touch_calibrated',translation:[1,2,3],joint_positions:{shoulder_pan:0,wrist_roll:.3}},layers:{robot:true}};
const original=structuredClone(manifest),renderer={update:(m,options)=>updates.push({m,options}),holdJointPose:()=>holds++};
const view=new LiveArmView(text=>badges.push(text),()=>now);
view.setContext(renderer,manifest,true);
const reading=()=>({state:'LIVE / READ-ONLY',updated:now});
const get=async()=>{calls++;return {joint_positions:{shoulder_pan:1.1}};};
await view.receive(reading(),get);
assert.equal(view.status,'ARM LIVE · READ-ONLY');assert.equal(updates.at(-1).m.robot.joint_positions.shoulder_pan,1.1);
assert.equal(updates.at(-1).m.robot.joint_positions.wrist_roll,.3);assert.equal(updates.at(-1).options.easeJoints,true);
assert.deepEqual(manifest,original);assert.notEqual(updates.at(-1).m,manifest);
await view.receive(reading(),get);assert.equal(calls,1); // Same sample is not animated twice.
now+=1.51;view.refresh();assert.equal(view.status,'ARM DATA STALE');assert.equal(updates.length,1);assert.ok(holds);
await view.receive({state:'LIVE / READ-ONLY',updated:100},get);assert.equal(calls,1);
await view.receive({state:'UNAVAILABLE'},get);assert.equal(view.status,'ARM DATA STALE');assert.equal(updates.length,1);
await view.receive({state:'NOT CONNECTED'},get);assert.equal(view.status,'ARM NOT LIVE');assert.deepEqual(updates.at(-1).m,original);
view.setEnabled(false);await view.receive(reading(),get);assert.equal(calls,1);assert.equal(view.status,'ARM NOT LIVE');
view.setEnabled(true);await view.receive(reading(),async()=>{throw Error('unavailable');});assert.equal(view.status,'ARM DATA STALE');
// A late response cannot animate stale readings, a switched workspace or a disabled view.
for(const interrupt of [()=>now+=2,()=>view.setContext(renderer,{robot:{joint_positions:{}}},true),()=>view.setEnabled(false)]){
 view.setContext(renderer,manifest,true);const before=updates.length;let resolve;
 const pending=view.receive(reading(),()=>new Promise(r=>resolve=r));interrupt();const after=updates.length;
 resolve({joint_positions:{shoulder_pan:2}});await pending;assert.equal(updates.length,after);assert.ok(after>=before);
}
assert.equal(validJoints({shoulder_pan:Infinity}),false);assert.equal(validJoints({shoulder_pan:'1'}),false);
assert.equal(freshArm({state:'RECORDED SAMPLE / NOT LIVE',updated:now},now),false);
const ease=new JointEase({joint:0},{joint:1},100,200);
assert.deepEqual(ease.value(100),{joint:0});assert.deepEqual(ease.value(200),{joint:.5});assert.deepEqual(ease.value(500),{joint:1});assert.deepEqual(ease.value(100,true),{joint:1});
console.log('Read-only active/draft pose overlays, stale/disconnected badges, async races and reduced-motion easing passed.');
