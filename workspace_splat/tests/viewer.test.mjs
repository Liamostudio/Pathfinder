import assert from 'node:assert/strict';
import {test} from 'node:test';
import {GaussianViewer} from '../web/viewer.js';

function harness(){
 const pending=new Map(),statuses=[];
 const viewer=Object.create(GaussianViewer.prototype);viewer.onStatus=s=>statuses.push(s);
 // Intentionally ignore AbortSignal: late results must still be harmless.
 globalThis.fetch=url=>new Promise((resolve,reject)=>pending.set(url,{resolve,reject}));
 function finish(base,id){
  const metadata={id,bounds:[[-1,-1,-1],[1,1,1]],cameras:[{world_to_camera:[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],K:[[1,0,0],[0,1,0],[0,0,1]],width:640,height:480}]};
  const raw=new ArrayBuffer(32),view=new DataView(raw);view.setFloat32(0,id,true);
  for(let i=3;i<6;i++)view.setFloat32(i*4,.03,true);
  for(let i=24;i<32;i++)view.setUint8(i,128);view.setUint8(28,255);
  pending.get(base+'/scene.json').resolve({ok:true,json:async()=>metadata});
  pending.get(base+'/scene.splat').resolve({ok:true,arrayBuffer:async()=>raw});
 }
 return {viewer,pending,finish,statuses};
}

test('late scene A cannot replace selected scene B',async()=>{
 const {viewer,finish,statuses}=harness();
 const a=viewer.load('/a'),b=viewer.load('/b');
 finish('/b',2);assert.equal((await b).id,2);
 finish('/a',1);assert.equal(await a,null);
 assert.equal(viewer.metadata.id,2);assert.equal(viewer.data[0],2);assert.equal(statuses.length,1);
});

test('switching to an unfinished capture cancels pending scene loads',async()=>{
 const {viewer,finish,statuses}=harness();
 const a=viewer.load('/a');viewer.clearScene();finish('/a',1);
 assert.equal(await a,null);assert.equal(viewer.data,null);assert.equal(viewer.metadata,null);assert.equal(statuses.length,0);
});

test('late error from an old scene cannot fail a successful new scene',async()=>{
 const {viewer,finish,pending}=harness();
 const a=viewer.load('/a'),b=viewer.load('/b');finish('/b',2);await b;
 pending.get('/a/scene.json').reject(Error('Old request failed'));
 assert.equal(await a,null);assert.equal(viewer.metadata.id,2);
});

test('orbit preserves distance, zoom changes distance, reset restores the captured view',async()=>{
 const {viewer,finish}=harness();const ready=viewer.load('/scene');finish('/scene',1);await ready;
 viewer.canvas={width:640,height:480};
 const distance=()=>Math.hypot(...viewer.centre().map((x,i)=>x-viewer.target[i]));
 const initial=distance();viewer.orbit(45,10);
 assert.ok(Math.abs(distance()-initial)<1e-8);
 viewer.zoom(.5);assert.ok(Math.abs(distance()-initial*.5)<1e-8);
 viewer.pan(20,5);assert.notDeepEqual(viewer.target,[0,0,0]);
 viewer.resetView();assert.deepEqual(viewer.target,[0,0,0]);assert.ok(Math.abs(distance()-initial)<1e-8);
 viewer.zoom(Infinity);assert.ok(Number.isFinite(distance()));
 viewer.zoom(1e-20);assert.ok(distance()>0);
});
