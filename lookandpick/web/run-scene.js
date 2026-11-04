// Shared live/replay presentation contract. Marker metres in; scene metres out.
import {mul,point,xyz} from './workspace-math.js';
import {stopView,shortStatus} from './motion-view.js';
export const finitePoint=p=>Array.isArray(p)&&p.length===3&&p.every(Number.isFinite);
export function markerMatrix(manifest){const a=manifest?.marker_alignment?.canonical_from_marker,s=manifest?.calibration?.meters_per_map_unit||1;return a?.length===4&&a.every(r=>r.length===4&&r.every(Number.isFinite))?a.map((r,i)=>i<3?r.map(x=>x*s):[...r]):null;}
export function runRobot(frame,manifest){
 const matrix=markerMatrix(manifest),offset=frame?.arm_base_offset;
 if(!matrix||!finitePoint(offset)||!frame.joints||!Object.keys(frame.joints).length)return null;
 const joints={};for(const [name,row] of Object.entries(frame.joints)){const value=typeof row==='number'?row:row?.position;if(!Number.isFinite(value))return null;joints[name]=value;}
 // Simulator joint values are URDF radians, with no real-arm draw offset.
 return {base:mul(matrix,xyz(offset)),joints};
}
export function runItems(frame,manifest){
 const matrix=markerMatrix(manifest);if(!matrix)return {objects:[],hands:[],tcp:null,scale:1};
 const scale=Math.hypot(...matrix.slice(0,3).map(r=>r[0])),tcp=finitePoint(frame?.scene?.arm_position)?point(matrix,frame.scene.arm_position):null;
 return {scale,tcp,objects:(frame?.scene?.objects||[]).filter(o=>finitePoint(o.position)).map(o=>({...o,position:o.held&&tcp?tcp:point(matrix,o.position),selected:o.id===frame.selected})),
 hands:(frame?.scene?.hands||[]).filter(h=>finitePoint(h.position)).map(h=>({...h,position:point(matrix,h.position),radius:Math.max(.015,Math.min(.3,Number.isFinite(h.radius_m)?h.radius_m:.04))*scale}))};
}
// The marker's axes in scene space (unit vectors). Views are defined in the MARKER frame (x right, y toward the wall, z up) because the scan's own
// axes have no relation to the real table: with them the camera could sit behind the table and put the arm on the wrong side.
export function markerAxes(manifest){const m=markerMatrix(manifest);if(!m)return null;const unit=i=>{const v=[m[0][i],m[1][i],m[2][i]],n=Math.hypot(...v)||1;return v.map(x=>x/n);};return {x:unit(0),y:unit(1),z:unit(2)};}
// Film camera: the view from where Liam stands, on the marker's -y side looking toward the wall, slightly right of centre, 38 degrees up; marker +x (the arm side) is on the right.
export function filmCamera(manifest){const table=manifest.surfaces.find(s=>s.name==='table'),s=manifest.calibration.meters_per_map_unit||1,target=[table.centre[0]*s,table.centre[1]*s,table.height*s+.02*s],span=Math.max(table.width,table.depth)*s,axes=markerAxes(manifest),local=[.25,-1,.8],n=Math.hypot(...local),dir=axes?[0,1,2].map(i=>(axes.x[i]*local[0]+axes.y[i]*local[1]+axes.z[i]*local[2])/n):[.65,-1.05,.86].map(v=>v/1.505);return {target,eye:target.map((v,i)=>v+dir[i]*span*1.505)};}
// Straight-down table view: marker +y is up the screen, so marker +x is on the right (the arm on the right, the checkerboard on the left).
export function topViewUp(manifest){const a=markerAxes(manifest);return a?a.y:[0,1,0];}
export function captionView(frame,{stale=false}={}){
 const events=frame?.events||[],last=predicate=>[...events].reverse().find(predicate),source=e=>String(e.source||'').toLowerCase();
 const command=last(e=>(['meta glasses','muse voice transcribe','voice transcribe','command'].includes(source(e))&&e.status!=='error'&&e.output!=='(no speech)')||/muse spark|muse glimmer|scripted model/i.test(e.source)&&e.input);
 const answer=last(e=>/muse spark|muse glimmer|scripted model/i.test(e.source)&&e.output);
 const speech=command&&['meta glasses','muse voice transcribe','voice transcribe'].includes(source(command)),pending=speech&&String(command.input).includes('awaiting confirmation');
 const stop=stopView(frame),armEvent=last(e=>['arm','safety'].includes(source(e))),arm=armEvent&&(!command||events.indexOf(armEvent)>events.indexOf(command))?shortStatus(events):'';
 return {command:String((speech?command.output||command.input:command?.input)||'Ready for an instruction').slice(0,240),answer:String((!command||events.indexOf(answer)>=events.indexOf(command)?answer?.output:'')||'').slice(0,320),status:stale?'Runtime updates lost · pose held':pending?'Speech ready · awaiting your confirmation':arm||(frame?.stopped?'Arm · stopped':frame?.speed_scale===0?'Pausing: a hand is in the way':frame?.speed_scale<1?'Slowing: a hand is nearby':frame?.busy?'Arm · instruction running':'Arm · ready'),
 stop:frame?.replay&&stop.title?{...stop,recovery:'Recorded stop · Return to live exits the take'}:stale&&stop.title?{...stop,recovery:'Runtime updates lost · recovery unavailable'}:stop,kind:frame?.stop?.kind==='fault'?'fault':'person',phase:frame?.replay?'Take '+(frame.take||1):frame?'Live':'Setup',speed:Number.isFinite(frame?.speed_scale)?Math.round(Math.max(0,Math.min(1,frame.speed_scale))*100):null};
}
// Small shared meshes. Reused GPU buffers; no per-frame geometry allocations for objects.
export function boxMesh(edges=false){const p=[[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],[-.5,-.5,.5],[.5,-.5,.5],[.5,.5,.5],[-.5,.5,.5]],out=[];
 if(edges){for(const [a,b] of [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]])for(const i of [a,b])out.push(...p[i],1,1,1,0,0,1);}
 else for(const [f,n] of [[[0,3,2,1],[0,0,-1]],[[4,5,6,7],[0,0,1]],[[0,1,5,4],[0,-1,0]],[[3,7,6,2],[0,1,0]],[[0,4,7,3],[-1,0,0]],[[1,2,6,5],[1,0,0]]])for(const i of [f[0],f[1],f[2],f[0],f[2],f[3]])out.push(...p[i],1,1,1,...n);
 return new Float32Array(out);}
export function sphereMesh(){const out=[],p=(a,b)=>[Math.cos(a)*Math.sin(b),Math.sin(a)*Math.sin(b),Math.cos(b)];for(let j=0;j<12;j++)for(let i=0;i<24;i++){const a=i*Math.PI/12,b=j*Math.PI/12,q=[p(a,b),p(a+Math.PI/12,b),p(a+Math.PI/12,b+Math.PI/12),p(a,b+Math.PI/12)];for(const k of [0,1,2,0,2,3])out.push(...q[k],1,1,1,...q[k]);}return new Float32Array(out);}
export function ringMesh(){const out=[];for(let i=0;i<=64;i++){const a=i*Math.PI/32;out.push(Math.cos(a),Math.sin(a),0,1,1,1,0,0,1);}return new Float32Array(out);}
export function modelAt(position,size){const m=xyz(position);(Array.isArray(size)?size:[size,size,size]).forEach((v,i)=>m[i][i]=v);return m;}
export function dashedRoute(vertices,dash=.018){
 const out=[];for(let i=9;i<vertices.length;i+=9){const a=Array.from(vertices.slice(i-9,i-6)),b=Array.from(vertices.slice(i,i+3)),d=b.map((v,k)=>v-a[k]),length=Math.hypot(...d);if(!length)continue;
  for(let start=0;start<length;start+=dash*1.65)for(const t of [start,Math.min(length,start+dash)])out.push(...a.map((v,k)=>v+d[k]*t/length),.84,.99,.5,0,0,1);
 }return new Float32Array(out);
}
// Existing seeded demo playback uses this same display contract; no control methods.
export class RecordedTake{
 constructor(data){
  if(!Array.isArray(data?.frames)||!data.frames.length||data.frames.length>18000||data.frames.some((f,i)=>!Number.isFinite(f.t)||f.t<0||(i&&f.t<data.frames[i-1].t)||!f.scene||!finitePoint(f.arm_base_offset)))throw Error('Take has no compatible 3D frames');
  this.frames=data.frames;this.events=(data.events||[]).filter(e=>Number.isFinite(e.t));this.duration=this.frames.at(-1).t;this.seed=data.seed;
 }
 frame(seconds){let lo=0,hi=this.frames.length;while(lo<hi){const mid=(lo+hi)>>1;if(this.frames[mid].t<=seconds)lo=mid+1;else hi=mid;}
  const f=this.frames[Math.max(0,lo-1)];return {...f,replay:true,take:1,backend:'sim',instance_id:'recorded-take',busy:seconds<this.duration&&!f.stopped,events:this.events.filter(e=>e.t<=f.t)};
 }
}
export function remainingRoute(vertices,tcp){
 if(!finitePoint(tcp)||vertices.length<18)return vertices;
 let best=null;for(let i=0;i<vertices.length-9;i+=9){const a=Array.from(vertices.slice(i,i+3)),d=Array.from(vertices.slice(i+9,i+12),(v,k)=>v-a[k]),length=d.reduce((s,v)=>s+v*v,0),t=length?Math.max(0,Math.min(1,d.reduce((s,v,k)=>s+v*(tcp[k]-a[k]),0)/length)):0,p=a.map((v,k)=>v+t*d[k]),distance=p.reduce((s,v,k)=>s+(v-tcp[k])**2,0);if(!best||distance<best.distance)best={i,p,distance};}
 return new Float32Array([...best.p,.84,.99,.5,0,0,1,...vertices.slice(best.i+9)]);
}
