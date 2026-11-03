import assert from 'node:assert/strict';
import {cellCorners,gridShape,footprintFromDrag,heightFromDrag,voxelLines,volumeCorners,remainingFreshness} from '../../lookandpick/web/workspace-voxels.js';
const table={centre:[1,2,0],width:1,depth:.6,height:.2,yaw:90};
const a=footprintFromDrag([1.2,1.8,.2],[.8,2.2,.2],table,.1,.3);
assert.deepEqual(a.centre,[1,2]);assert.ok(Math.abs(a.width-.4)<1e-6);assert.ok(Math.abs(a.depth-.4)<1e-6);assert.equal(a.base,.2);assert.equal(a.yaw,90);
assert.deepEqual(gridShape(a),[4,4,3]);assert.equal(cellCorners(a,0).length,8);assert.equal(volumeCorners(a).length,8);
assert.equal(voxelLines(a,null).length,4*4*3*24*9);
assert.equal(heightFromDrag(.3,[0,-50],[0,-100],.05),.8);
assert.equal(heightFromDrag(.3,[0,100],[0,-100],.05),.05);
const clamped=footprintFromDrag([100,100,0],[-100,-100,0],table,.1,.3);assert.ok(clamped.width<=table.width+1e-8&&clamped.depth<=table.depth+1e-8);
assert.throws(()=>voxelLines({...a,width:10,depth:10,height:10},null),/4,096/);
console.log('Voxel footprint, cube topology, bounds, extrusion and rendering budget passed.');

assert.equal(remainingFreshness({valid_for_ms:300,generated_at_ms:1000},500,1200),100);
assert.equal(remainingFreshness({valid_for_ms:300,generated_at_ms:1000},500,1400),0);
