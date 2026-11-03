import {test} from 'node:test';
import assert from 'node:assert/strict';
import {I,xyz,point,cameraInFrame,transformGaussians,resizeRectangle,rectangle} from '../../lookandpick/web/workspace-math.js';
const near=(a,b)=>a.forEach((x,i)=>assert.ok(Math.abs(x-b[i])<1e-5,`${a} != ${b}`));
test('camera inversion retains projection under rotated translated scaled workspace frame',()=>{
 const camera={world_to_camera:[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]]};
 const frame=xyz([2,3,4],[.2,-.3,.7],2),next=cameraInFrame(camera,frame),world=[.2,.4,.1];
 const old=point(camera.world_to_camera,world),now=point(next.world_to_camera,point(frame,world));
 near(now,old.map(x=>x*2));near([now[0]/now[2],now[1]/now[2]],[old[0]/old[2],old[1]/old[2]]);
});
test('Gaussian normalisation transforms positions sizes and orientations together',()=>{
 const frame=xyz([3,2,1],[0,0,Math.PI/2],2),viewer={data:new Float32Array([1,0,0,.1,.2,.3,1,0,0,0,1,1,1,1]),metadata:{cameras:[]},target:[0,0,0]};
 const manifest={frames:{workspace_from_map:frame,gaussian_normalization:{centre:[1,0,0],radius:3}}};transformGaussians(viewer,manifest);
 near([...viewer.data.slice(0,3)],[3,10,1]);near([...viewer.data.slice(3,6)],[.6,1.2,1.8]);near([...viewer.data.slice(6,10)],[Math.SQRT1_2,0,0,Math.SQRT1_2]);
});
test('corner resize retains rectangular orientation and fixed opposite corner',()=>{
 const s={name:'table',centre:[0,0,0],width:2,depth:1,height:0,yaw:30};const before=rectangle(s),position=[2,2,0],after=resizeRectangle(s,2,position),corners=rectangle(after);near(corners[0],before[0]);near(corners[2],position);assert.equal(after.yaw,30);
});
