import assert from 'node:assert/strict';
import {voxelStyle,voxelStatus,allVoxelClasses,filterActive} from '../../lookandpick/web/voxel-display.js';
import {VoxelCubes} from '../../lookandpick/web/voxel-cubes.js';
const all=allVoxelClasses(),intrusions={allowed:false,unknown:false,manual:true};
for(const observed of ['free','unknown','occupied'])for(const blocked of [false,true]){
 assert.equal(voxelStyle(observed,blocked,'monitor',all).visible,true);
 assert.equal(voxelStyle(observed,blocked,'monitor',intrusions).visible,blocked||observed==='occupied');
 assert.equal(voxelStyle(observed,blocked,'edit',intrusions).visible,true);
 assert.equal(voxelStyle(observed,blocked,'monitor',{allowed:false,unknown:false,manual:false,live:false}).visible,observed==='occupied');
}
assert.notDeepEqual(voxelStyle('occupied',false,'monitor',all).colour,voxelStyle('unknown',true,'monitor',all).colour);
const snap={enabled:true,fresh:false,counts:{free:1,occupied:2,unknown:1},states:['free','unknown','occupied','occupied'],observed_states:['free','unknown','unknown','occupied'],manual_blocked:[2]};
const before=structuredClone(snap);
assert.equal(voxelStatus(snap,intrusions,'monitor'),'1 clear · 2 unsafe · 1 unknown · no fresh observations · display filter: intrusions only');
assert.equal(voxelStatus(snap,all,'monitor'),'1 clear · 2 unsafe · 1 unknown · no fresh observations');
assert.equal(filterActive(intrusions,'edit'),false);
// Exercise the actual instance loop: filters change alpha only, not evidence or policy.
const gl=new Proxy({ARRAY_BUFFER:1,DYNAMIC_DRAW:2},{get:(target,key)=>target[key]??(()=>{})});
const area={enabled:true,centre:[0,0],width:4,depth:1,height:1,base:0,yaw:0,cell_size:1};
const cubes=Object.create(VoxelCubes.prototype);
Object.assign(cubes,{gl,renderer:{cad:{clientWidth:400,clientHeight:400},canvasRay:()=>null},area,count:4,centres:[[-1.5,0,.5],[-.5,0,.5],[.5,0,.5],[1.5,0,.5]],extents:Array(4).fill([1,1,1]),sizes:new Float32Array(4).fill(.44),velocity:new Float32Array(4),instances:new Float32Array(44),scale:1,reveal:0,hover:null,row:[],single:null,reduced:true,mode:'monitor',stage:'vertex',manual:new Set(snap.manual_blocked),observed:snap.observed_states,snapshot:snap,uniforms:{},drawHandles(){}});
const camera={rotation:[[1,0,0],[0,1,0],[0,0,1]],translation:[0,0,3],K:[[100,0,200],[0,100,200],[0,0,1]],width:400,height:400};
const alpha=()=>[0,1,2,3].map(i=>cubes.instances[i*11+7]);
cubes.displayFilter=all;cubes.draw(camera,{width:400,height:400},100);assert.ok(alpha().every(x=>x>0));
cubes.displayFilter=intrusions;cubes.draw(camera,{width:400,height:400},110);assert.deepEqual(alpha().map(x=>x>0),[false,false,true,true]);
assert.ok(alpha()[3]>alpha()[2]);
cubes.displayFilter={allowed:true,unknown:true,manual:false};cubes.draw(camera,{width:400,height:400},120);assert.deepEqual(alpha().map(x=>x>0),[true,true,false,true]);
cubes.mode='edit';cubes.draw(camera,{width:400,height:400},130);assert.ok(alpha().every(x=>x>0));
assert.deepEqual(snap,before);assert.deepEqual([...cubes.manual],before.manual_blocked);
// Hidden cells cannot be toggled by an accidental double-click in monitor mode.
cubes.mode='monitor';cubes.displayFilter=intrusions;cubes.enabled=true;cubes.toggleAt({});
console.log('Instance alpha filtering, immutable evidence/counts, always-visible intrusions and stale status passed.');
