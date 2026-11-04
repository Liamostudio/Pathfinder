import test from 'node:test';
import assert from 'node:assert/strict';
import {frameIndex,nearestEvent,latestEvent,scrubTime,advanceTime} from '../../replay_site/playback.mjs';
const frames=[{t:0},{t:.1},{t:.2},{t:1}];
test('frame lookup holds the latest recorded frame at boundaries and gaps',()=>{
  assert.equal(frameIndex([],0),-1);
  for(const [t,i] of [[-1,0],[0,0],[.099,0],[.1,1],[.8,2],[1,3],[8,3]])assert.equal(frameIndex(frames,t),i);
});
test('scrubbing clamps input and supports dragging backwards',()=>{
  assert.equal(scrubTime(-5,1),0);assert.equal(scrubTime(5,1),1);
  assert.equal(scrubTime('0.15',1),.15);assert.equal(scrubTime('bad',1),0);
  assert.equal(frameIndex(frames,scrubTime(.8,1)),2);
  assert.equal(frameIndex(frames,scrubTime(.05,1)),0);
});
test('nearest timeline event and earlier tie',()=>{
  assert.equal(nearestEvent([],1),-1);assert.equal(nearestEvent(frames,.18),2);
  assert.equal(nearestEvent(frames,.05),0);assert.equal(nearestEvent(frames,100),3);
});
test('captions never show future events and rewind correctly',()=>{
  const events=[{t:.1,source:'voice'},{t:.2,source:'model'},{t:.8,source:'voice'}];
  assert.equal(latestEvent(events,0,()=>true),null);
  assert.equal(latestEvent(events,.9,e=>e.source==='voice'),events[2]);
  assert.equal(latestEvent(events,.15,e=>e.source==='voice'),events[0]);
});
test('speed and final frame obey elapsed wall time',()=>{
  assert.equal(advanceTime(.1,.2,.5,1),.2);assert.equal(advanceTime(.1,.2,2,1),.5);
  assert.equal(advanceTime(.9,.5,1,1),1);assert.equal(advanceTime(.1,-1,1,1),.1);
});
