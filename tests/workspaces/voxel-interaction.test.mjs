import assert from 'node:assert/strict';
import {gridShape,rowIndices,cellCoordinates,cellCentre,pickVoxel,edgeAxis,springStep,fidelityCellSize} from '../../lookandpick/web/workspace-voxels.js';
const a={centre:[.2,-.1],width:.6,depth:.4,height:.3,base:.1,yaw:30,cell_size:.1};
for(let axis=0;axis<3;axis++){const index=13,row=rowIndices(a,index,axis),fixed=cellCoordinates(a,index);assert.equal(row.length,gridShape(a)[axis]);for(const id of row)cellCoordinates(a,id).forEach((v,k)=>{if(k!==axis)assert.equal(v,fixed[k]);});}
assert.equal(edgeAxis(0,1),0);assert.equal(edgeAxis(0,3),1);assert.equal(edgeAxis(0,4),2);
const centre=cellCentre(a,0),hit=pickVoxel(a,[centre[0],centre[1],2],[0,0,-1]);assert.ok(hit);assert.equal(hit.index%24,0);assert.equal(pickVoxel(a,[99,99,2],[0,0,-1]),null);
let value=.44,velocity=0,peak=value;for(let i=0;i<240;i++){[value,velocity]=springStep(value,velocity,.88,1/120);peak=Math.max(peak,value);}assert.ok(peak>.96&&peak<=1.02);assert.ok(Math.abs(value-.88)<.001);assert.deepEqual(springStep(.44,0,.88,.02,true),[.88,0]);
const volume={...a,width:.640256863,depth:.523846525,height:.582051694};let last=Infinity;for(const budget of [256,1024,4096]){const size=fidelityCellSize(volume,budget,1.164103388);assert.ok(size<=last);last=size;assert.ok(gridShape({...volume,cell_size:size}).reduce((x,y)=>x*y,1)<=budget);}
console.log('Voxel ray picking, row axes, spring settling and bounded fidelity presets passed.');

const {sliceIndices,sliceVisible}=await import('../../lookandpick/web/workspace-voxels.js');
const slab={centre:[0,0],width:3,depth:3,height:3,base:0,yaw:0,cell_size:1};
assert.equal(sliceIndices(slab,0,1).length,9);
assert(sliceIndices(slab,2,1).every(i=>Math.floor(i/9)===1));
assert.equal(sliceVisible(slab,2,0,1,[10,0,1]),false);
assert.equal(sliceVisible(slab,0,0,1,[10,0,1]),true);
assert.equal(sliceVisible(slab,0,0,1,[-10,0,1]),false);
assert.equal(sliceVisible({...slab,yaw:90},2,0,1,[0,10,1]),false);
console.log('Axis slices and camera-side clipping passed.');
const {VoxelCubes}=await import('../../lookandpick/web/voxel-cubes.js');
const picker={stage:'cells',single:4,vertex:{index:3},row:[3,4],axis:0,emit(){},sliceOrigin:[0,0]};
VoxelCubes.prototype.back.call(picker);assert.equal(picker.stage,'slice');assert.equal(picker.single,null);
VoxelCubes.prototype.back.call(picker);assert.equal(picker.stage,'axis');assert.equal(picker.axis,null);
VoxelCubes.prototype.back.call(picker);assert.equal(picker.stage,'vertex');assert.equal(picker.vertex,null);
console.log('Frozen layer → moving layers → axis → vertex backtracking passed.');
