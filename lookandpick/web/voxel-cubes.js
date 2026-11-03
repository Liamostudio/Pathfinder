import {gridShape,cellCentre,cellCoordinates,scaledCorners,EDGE_PAIRS,edgeAxis,rowIndices,pickVoxel,springStep} from './workspace-voxels.js';

const vs=`#version 300 es
precision highp float;
layout(location=0) in vec3 vertex;layout(location=1) in vec3 normal;
layout(location=2) in vec3 centre;layout(location=3) in vec3 colour;layout(location=4) in vec2 appearance;layout(location=5) in vec3 extent;
uniform mat3 cameraRotation;uniform vec3 translation;uniform vec4 intrinsics;uniform vec2 viewport;uniform vec2 yaw;uniform float cellSize;uniform float edges;
out vec4 rgba;
void main(){vec3 local=vertex*extent*appearance.x;vec3 world=centre+vec3(yaw.x*local.x-yaw.y*local.y,yaw.y*local.x+yaw.x*local.y,local.z);vec3 p=cameraRotation*world+translation;
vec2 px=intrinsics.xy*p.xy/max(p.z,.00001)+intrinsics.zw;float near=.01,far=100.;gl_Position=vec4((2.*px.x/viewport.x-1.)*p.z,(1.-2.*px.y/viewport.y)*p.z,((far+near)/(far-near))*p.z-2.*far*near/(far-near),p.z);
float light=edges>0.?1.:.65+.35*abs(dot(normal,normalize(vec3(.3,-.4,1.))));float opacity=appearance.y*(edges>0.?1.:.38);rgba=vec4(colour*light,opacity);}`;
const fs=`#version 300 es
precision highp float;in vec4 rgba;out vec4 result;void main(){result=rgba;}`;
const ns='http://www.w3.org/2000/svg';
const svgNode=(name,attrs={})=>{const node=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node;};
const transpose3=m=>new Float32Array([m[0][0],m[1][0],m[2][0],m[0][1],m[1][1],m[2][1],m[0][2],m[1][2],m[2][2]]);
const corners=[[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],[-.5,-.5,.5],[.5,-.5,.5],[.5,.5,.5],[-.5,.5,.5]];

export class VoxelCubes{
 constructor(renderer){
  this.renderer=renderer;this.gl=renderer.gl;this.mode='monitor';this.hover=null;this.stage='vertex';this.vertex=null;this.axis=null;this.row=[];this.reveal=0;this.enabled=true;this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;this.velocity=[];this.pending=Promise.resolve();this.listeners=[];
  const gl=this.gl,compile=(type,text)=>{const s=gl.createShader(type);gl.shaderSource(s,text);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
  const a=compile(gl.VERTEX_SHADER,vs),b=compile(gl.FRAGMENT_SHADER,fs);this.program=gl.createProgram();gl.attachShader(this.program,a);gl.attachShader(this.program,b);gl.linkProgram(this.program);gl.deleteShader(a);gl.deleteShader(b);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  this.uniforms=Object.fromEntries(['cameraRotation','translation','intrinsics','viewport','yaw','cellSize','edges'].map(k=>[k,gl.getUniformLocation(this.program,k)]));
  const faces=[];for(const [f,n] of [[[0,3,2,1],[0,0,-1]],[[4,5,6,7],[0,0,1]],[[0,1,5,4],[0,-1,0]],[[3,7,6,2],[0,1,0]],[[0,4,7,3],[-1,0,0]],[[1,2,6,5],[1,0,0]]])for(const i of [f[0],f[1],f[2],f[0],f[2],f[3]])faces.push(...corners[i],...n);
  const lines=[];for(const edge of EDGE_PAIRS)for(const i of edge)lines.push(...corners[i],0,0,1);
  this.faceBuffer=this.makeBuffer(new Float32Array(faces));this.edgeBuffer=this.makeBuffer(new Float32Array(lines));this.instanceBuffer=this.makeBuffer(new Float32Array(0));this.vao=gl.createVertexArray();
  this.svg=svgNode('svg',{'class':'voxel-handles','aria-label':'Voxel vertex and axis selection'});renderer.cad.parentElement.append(this.svg);this.label=document.createElement('span');this.label.className='voxel-inspector';renderer.cad.parentElement.append(this.label);
  this.attach();
 }
 makeBuffer(values){const b=this.gl.createBuffer();this.gl.bindBuffer(this.gl.ARRAY_BUFFER,b);this.gl.bufferData(this.gl.ARRAY_BUFFER,values,this.gl.DYNAMIC_DRAW);return b;}
 listen(target,name,fn,options){target.addEventListener(name,fn,options);this.listeners.push(()=>target.removeEventListener(name,fn,options));}
 attach(){
  const canvas=this.renderer.cad;
  this.listen(canvas,'pointermove',e=>{if(!this.area?.enabled||!this.enabled||e.buttons)return;const r=canvas.getBoundingClientRect();this.pointer=[e.clientX-r.left,e.clientY-r.top];const ray=this.renderer.canvasRay(...this.pointer);if(!ray)return;const scale=this.scale;const hit=pickVoxel(this.area,ray.origin.map(v=>v/scale),ray.direction,this.sizes,this.hover);this.setHover(hit?.index??null);});
  this.listen(canvas,'pointerleave',e=>{if(canvas.parentElement.contains(e.relatedTarget))return;if(!this.vertex)this.setHover(null);});
  this.listen(canvas,'dblclick',e=>this.toggleAt(e),true);
  this.listen(canvas,'pointerdown',e=>{if(e.button===2&&this.stage!=='vertex'&&this.enabled){e.preventDefault();e.stopImmediatePropagation();this.back();}},true);
  this.listen(canvas,'contextmenu',e=>{if(this.area?.enabled)e.preventDefault();});
  this.listen(this.svg,'contextmenu',e=>e.preventDefault());
  this.listen(this.svg,'pointerdown',e=>{e.preventDefault();e.stopPropagation();if(e.button===2){this.back();return;}if(e.button!==0)return;const t=e.target;clearTimeout(this.clickTimer);if(t.dataset.corner!==undefined){this.clickTimer=setTimeout(()=>{this.single=null;this.vertex={index:Number(t.dataset.index),corner:Number(t.dataset.corner)};this.stage='axis';this.axis=null;this.row=[];this.svgKey=null;this.emit();},320);}else if(t.dataset.axis!==undefined){this.axis=Number(t.dataset.axis);this.row=rowIndices(this.area,this.vertex.index,this.axis);this.stage='row';this.svgKey=null;this.emit();}});
  this.listen(this.svg,'dblclick',e=>this.toggleAt(e));
 }
 toggleAt(e){if(!this.area?.enabled||!this.enabled)return;clearTimeout(this.clickTimer);const r=this.renderer.cad.getBoundingClientRect(),ray=this.renderer.canvasRay(e.clientX-r.left,e.clientY-r.top);if(!ray)return;const hit=pickVoxel(this.area,ray.origin.map(v=>v/this.scale),ray.direction,this.sizes,this.hover),index=hit?.index??(e.target.dataset.corner!==undefined?Number(e.target.dataset.index):null);if(index===null)return;e.preventDefault();e.stopImmediatePropagation();this.vertex=null;this.row=[];this.axis=null;this.stage='vertex';this.single=index;this.setHover(index);this.setMode('edit');this.edit([index],null);}
 setHover(index){if(index!==this.hover){this.hover=index;this.svgKey=null;this.emit();}}
 setMode(mode){this.mode=mode;this.renderer.onVoxelMode?.(mode);this.emit();}
 setEnabled(enabled){this.enabled=enabled;if(!enabled){this.hover=null;this.back(true);}this.svg.style.display=enabled?'':'none';}
 setArea(area,alignment,snapshot){
  const key=area?.enabled?JSON.stringify([area.centre,area.width,area.depth,area.height,area.base,area.yaw,area.cell_size]):null;
  if(key!==this.geometry){clearTimeout(this.clickTimer);this.single=null;this.generation=(this.generation||0)+1;this.geometry=key;this.area=area?structuredClone(area):null;this.count=area?.enabled?gridShape(area).reduce((a,b)=>a*b,1):0;this.centres=Array.from({length:this.count},(_,i)=>cellCentre(area,i));const oldSizes=this.sizes;this.sizes=new Float32Array(this.count).fill(.44);if(oldSizes&&this.reveal)for(let i=0;i<Math.min(oldSizes.length,this.count);i++)this.sizes[i]=oldSizes[i];this.velocity=new Float32Array(this.count);this.extents=Array.from({length:this.count},(_,i)=>cellCoordinates(area,i).map((v,k)=>Math.min(area.cell_size,[area.width,area.depth,area.height][k]-v*area.cell_size)));this.instances=new Float32Array(this.count*11);this.manual=new Set();this.policyRevision=-1;this.observedAt=0;this.hover=null;this.vertex=null;this.row=[];this.stage='vertex';this.axis=null;this.cameraKey=null;this.svgKey=null;this.emit();}
  else this.area=area?structuredClone(area):null;
  this.scale=this.renderer.manifest?.calibration.meters_per_map_unit||1;
  if(snapshot){this.snapshot=snapshot;if((snapshot.policy_revision??0)>=this.policyRevision){this.policyRevision=snapshot.policy_revision??0;this.manual=new Set(snapshot.manual_blocked||[]);}if((snapshot.generated_at_ms??0)>=this.observedAt){this.observedAt=snapshot.generated_at_ms??0;this.observed=snapshot.observed_states||snapshot.states||[];}}else{this.snapshot=null;this.observed=[];}this.updateLabel();
 }
 back(all=false){clearTimeout(this.clickTimer);if(all)this.single=null;if(all||this.stage==='axis'){this.stage='vertex';this.vertex=null;this.row=[];this.axis=null;}else if(this.stage==='row'){this.stage='axis';this.row=[];this.axis=null;}this.svgKey=null;this.emit();}
 updateLabel(){if(!this.label)return;const index=this.vertex?.index??this.hover??this.single,sensor=index===null||index===undefined?'':` · voxel ${index} · manual ${this.manual.has(index)?'blocked':'unblocked'} · live ${this.observed?.[index]||'unknown'}`;this.label.hidden=!this.area?.enabled||this.renderer.mode!=='cad';this.label.textContent=this.errorUntil>performance.now()?this.lastError:(this.mode==='edit'?'LAYOUT EDIT · green = manually unblocked, not live clearance':'LIVE STATUS · unknown cells remain unsafe')+sensor+(this.stage==='row'?` · ${'XYZ'[this.axis]} row: ${this.row.length} voxels`:this.stage==='axis'?' · choose an axis edge':index!==null&&index!==undefined?' · click a vertex':'');}
 emit(){this.updateLabel();this.renderer.onVoxelSelection?.({stage:this.stage,voxel:this.vertex?.index??this.hover??this.single,axis:this.axis,row:[...this.row],mode:this.mode});}
 edit(indices,blocked){if(!this.renderer.onVoxelEdit||!indices.length)return;const generation=this.generation,action=this.renderer.onVoxelEdit;this.pending=this.pending.then(()=>{if(generation!==this.generation)throw Error('Working area changed; select the voxel again');return action(indices,blocked);}).catch(e=>{this.lastError=e.message;this.errorUntil=performance.now()+3500;this.updateLabel();this.renderer.onVoxelError?.(e.message);});}
 canonicalCorners(index){return scaledCorners(this.area,index,this.sizes[index]||.44);}
 drawHandles(){
  this.updateLabel();const r=this.renderer;if(!this.area?.enabled||!this.enabled||r.mode!=='cad'){this.svg.replaceChildren();this.svgKey=null;return;}
  const index=this.vertex?.index??this.hover??this.single;if(index===null||index===undefined){this.svg.replaceChildren();this.svgKey=null;return;}
  const key=[this.stage,index,this.vertex?.corner].join('/');if(key!==this.svgKey){this.svgKey=key;this.svg.replaceChildren();
   if(this.stage==='vertex')for(let i=0;i<8;i++){const circle=svgNode('circle',{'r':4.5,'class':'voxel-vertex','data-corner':i,'data-index':index,'aria-label':`Voxel ${index} vertex ${i+1}`});this.svg.append(circle);}
   else{const origin=this.vertex.corner;for(const edge of EDGE_PAIRS.filter(e=>e.includes(origin))){const next=edge.find(i=>i!==origin),axis=edgeAxis(origin,next),group=svgNode('g',{'data-axis':axis});const line=svgNode('line',{'class':'voxel-axis','data-axis':axis,'data-index':index,'data-next':next,'aria-label':`Select ${'XYZ'[axis]} voxel row`});line.style.stroke=['#fc7a74','#b7f48a','#82bfff'][axis];const text=svgNode('text',{'data-axis':axis,'data-index':index,'data-next':next});text.textContent='XYZ'[axis];const hit=line.cloneNode();hit.setAttribute('class','voxel-axis-hit');hit.style.stroke='transparent';hit.style.strokeOpacity='0';group.append(line,hit,text);this.svg.append(group);}}
  }
  this.svg.setAttribute('viewBox',`0 0 ${r.cad.clientWidth} ${r.cad.clientHeight}`);const world=this.canonicalCorners(index),points=world.map(p=>r.project(p.map(v=>v*this.scale)));
  if(this.stage==='vertex'){for(const dot of this.svg.children){const p=points[Number(dot.dataset.corner)];dot.style.display=p?'':'none';if(p){dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);}}}
  else{const start=world[this.vertex.corner],p=r.project(start.map(v=>v*this.scale));for(const group of this.svg.children){const line=group.querySelector('line'),text=group.querySelector('text'),next=world[Number(line.dataset.next)],end=start.map((v,i)=>v+(next[i]-v)*2.8),q=r.project(end.map(v=>v*this.scale));group.style.display=p&&q?'':'none';if(p&&q){for(const l of group.querySelectorAll('line'))for(const [k,v] of Object.entries({x1:p[0],y1:p[1],x2:q[0],y2:q[1]}))l.setAttribute(k,v);text.setAttribute('x',q[0]+6);text.setAttribute('y',q[1]-5);}}}
 }
 draw(camera,canvas,time){
  if(!this.area?.enabled||!this.count){this.drawHandles();return;}const gl=this.gl,dt=Math.min(.034,(time-(this.lastTime||time-16))/1000);this.lastTime=time;
  const focus=this.hover!==null?this.centres[this.hover]:this.vertex?this.centres[this.vertex.index]:null,selected=new Set(this.row),camKey=JSON.stringify([camera.rotation,camera.translation]);
  if(camKey!==this.cameraKey){this.cameraKey=camKey;this.order=Array.from({length:this.count},(_,i)=>i).sort((a,b)=>{const z=p=>camera.rotation[2].reduce((s,x,i)=>s+x*p[i]*this.scale,camera.translation[2]);return z(this.centres[b])-z(this.centres[a]);});}
  for(let slot=0;slot<this.count;slot++){
   const index=this.order[slot],centre=this.centres[index],d=focus?Math.hypot(...centre.map((v,i)=>(v-focus[i])/this.area.cell_size)):100,near=Math.exp(-d*d/3.5),reveal=Math.max(this.reveal,this.reduced?0:Math.max(0,1-Math.max(0,time-(this.cameraRevealUntil||0))/400)),chosen=selected.has(index)||index===this.single,target=.44+.44*Math.max(near,reveal,chosen?.8:0);[this.sizes[index],this.velocity[index]]=springStep(this.sizes[index],this.velocity[index],target,dt,this.reduced);
   const observed=this.observed?.[index]||'unknown',blocked=this.manual.has(index),unsafe=observed==='occupied'||blocked;
   let colour=unsafe?[1,.27,.23]:this.mode==='edit'?[.48,.90,.66]:observed==='free'?[.38,.87,.52]:[.76,.66,.42];
   if(chosen&&!unsafe)colour=colour.map((v,i)=>v*.5+[.55,.83,1][i]*.5);
   const alpha=unsafe?.45:chosen?.42:.055+.46*Math.max(near,reveal);
   this.instances.set([...centre.map(v=>v*this.scale),...colour,this.sizes[index],alpha,...this.extents[index].map(v=>v*this.scale)],slot*11);
  }
  gl.useProgram(this.program);gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.instanceBuffer);gl.bufferData(gl.ARRAY_BUFFER,this.instances,gl.DYNAMIC_DRAW);
  for(const [location,size,offset] of [[2,3,0],[3,3,12],[4,2,24],[5,3,32]]){gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,gl.FLOAT,false,44,offset);gl.vertexAttribDivisor(location,1);}
  gl.uniformMatrix3fv(this.uniforms.cameraRotation,false,transpose3(camera.rotation));gl.uniform3fv(this.uniforms.translation,camera.translation);gl.uniform2f(this.uniforms.viewport,canvas.width,canvas.height);const fit=Math.min(canvas.width/camera.width,canvas.height/camera.height);gl.uniform4f(this.uniforms.intrinsics,camera.K[0][0]*fit,camera.K[1][1]*fit,camera.K[0][2]*fit+(canvas.width-camera.width*fit)/2,camera.K[1][2]*fit+(canvas.height-camera.height*fit)/2);const yaw=this.area.yaw*Math.PI/180;gl.uniform2f(this.uniforms.yaw,Math.cos(yaw),Math.sin(yaw));gl.uniform1f(this.uniforms.cellSize,this.area.cell_size*this.scale);
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);gl.enable(gl.DEPTH_TEST);
  for(const [buffer,mode,count,edges] of [[this.faceBuffer,gl.TRIANGLES,36,0],[this.edgeBuffer,gl.LINES,24,1]]){gl.bindBuffer(gl.ARRAY_BUFFER,buffer);for(let i=0;i<2;i++){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,3,gl.FLOAT,false,24,i*12);gl.vertexAttribDivisor(i,0);}gl.uniform1f(this.uniforms.edges,edges);gl.drawArraysInstanced(mode,0,count,this.count);}
  gl.depthMask(true);gl.disable(gl.BLEND);gl.bindVertexArray(null);this.drawHandles();
 }
 dispose(){clearTimeout(this.clickTimer);for(const remove of this.listeners)remove();this.svg.remove();this.label.remove();const gl=this.gl;for(const b of [this.faceBuffer,this.edgeBuffer,this.instanceBuffer])gl.deleteBuffer(b);gl.deleteVertexArray(this.vao);gl.deleteProgram(this.program);}
}
