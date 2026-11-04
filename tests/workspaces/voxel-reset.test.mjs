import assert from 'node:assert/strict';
import {clearManualBlocks} from '../../lookandpick/web/voxel-policy.js';
import {VoxelCubes} from '../../lookandpick/web/voxel-cubes.js';

// Clear the latest server policy, including blocks not present in the last UI poll.
const calls=[],root='/api/workspaces/example/versions/v0001';
const snapshot={revision:'volume-1',policy_revision:4,manual_blocked:[2,8],observed_states:['occupied','unknown']};
const result=await clearManualBlocks(root,async(path,body)=>{calls.push({path,body});return body?{...snapshot,policy_revision:5,manual_blocked:[]}:snapshot;});
assert.equal(result.cleared,2);assert.deepEqual(result.snapshot.observed_states,snapshot.observed_states);
assert.deepEqual(calls,[{path:root+'/voxels',body:undefined},{path:root+'/voxel-edits',body:{revision:'volume-1',policy_revision:4,indices:[2,8],blocked:false}}]);
let reads=0;await clearManualBlocks(root,async()=>{reads++;return {manual_blocked:[]};});assert.equal(reads,1);
await assert.rejects(clearManualBlocks(root,async(_,body)=>{if(body)throw Error('Voxel policy changed');return snapshot;}),/policy changed/);
let current=true,requests=0;
await assert.rejects(clearManualBlocks(root,async()=>{requests++;current=false;return snapshot;},()=>current),/Working area changed/);
assert.equal(requests,1); // A delayed GET cannot clear a different workspace/volume.

// Reset waits for already queued edits, exits clipping, and allows later explicit edits.
const order=[],errors=[];let release;
const cubes=Object.create(VoxelCubes.prototype);
Object.assign(cubes,{generation:1,pending:Promise.resolve(),stage:'cells',single:2,vertex:{index:2},axis:0,row:[2,8],emit(){},updateLabel(){},renderer:{onVoxelEdit:async(indices)=>{if(indices[0]===2)await new Promise(r=>release=r);order.push('block '+indices[0]);},onVoxelClear:async()=>order.push('reset'),onVoxelError:e=>errors.push(e)}});
const first=cubes.edit([2],true);await Promise.resolve();
const clear=cubes.clearManual(),last=cubes.edit([8],true);
assert.equal(cubes.stage,'vertex');assert.equal(cubes.axis,null);assert.deepEqual(cubes.row,[]);
release();await Promise.all([first,clear,last]);assert.deepEqual(order,['block 2','reset','block 8']);
// A changed volume invalidates queued operations without preventing a later retry.
const stale=cubes.clearManual();cubes.generation++;
await assert.rejects(stale,/Working area changed/);await cubes.pending;
await cubes.clearManual();assert.equal(order.at(-1),'reset');assert.equal(errors.length,1);
console.log('Manual reset preserves observations, respects edit order and exits slice clipping.');
