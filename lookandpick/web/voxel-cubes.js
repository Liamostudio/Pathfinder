import {gridShape,cellCentre,cellCoordinates,scaledCorners,EDGE_PAIRS,edgeAxis,pickVoxel,springStep,sliceIndices,sliceVisible} from './workspace-voxels.js';

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
  this.controls=document.createElement('div');this.controls.className='voxel-slice-controls';this.controls.hidden=true;
  this.controls.innerHTML='<span class="voxel-slice-name"></span><button type="button" aria-label="Previous voxel slice">−</button><input type="range" min="0" step="1" aria-label="Selected voxel slice"><button type="button" aria-label="Next voxel slice">+</button><button type="button" aria-label="Exit voxel slice">Back ↶</button>';
  renderer.cad.parentElement.append(this.controls);const buttons=this.controls.querySelectorAll('button');buttons[0].onclick=()=>{this.sliceOrigin=null;this.setSlice(this.layer-1);};buttons[1].onclick=()=>{this.sliceOrigin=null;this.setSlice(this.layer+1);};buttons[2].onclick=()=>this.back();this.controls.querySelector('input').oninput=e=>{this.sliceOrigin=null;this.setSlice(Number(e.target.value));};
  this.listen(this.controls,'pointerdown',e=>e.stopPropagation());this.listen(this.controls,'contextmenu',e=>{e.preventDefault();this.back();});
  this.attach();
 }
 makeBuffer(values){const b=this.gl.createBuffer();this.gl.bindBuffer(this.gl.ARRAY_BUFFER,b);this.gl.bufferData(this.gl.ARRAY_BUFFER,values,this.gl.DYNAMIC_DRAW);return b;}
 listen(target,name,fn,options){target.addEventListener(name,fn,options);this.listeners.push(()=>target.removeEventListener(name,fn,options));}
 attach(){
  const canvas=this.renderer.cad;
  this.listen(canvas,'pointermove',e=>{if(!this.area?.enabled||!this.enabled||(e.buttons&&!['slice','cells'].includes(this.stage)))return;const r=canvas.getBoundingClientRect();this.pointer=[e.clientX-r.left,e.clientY-r.top];if(this.stage==='slice')this.moveSlice(e);const ray=this.renderer.canvasRay(...this.pointer);if(!ray)return;const hit=pickVoxel(this.area,ray.origin.map(v=>v/this.scale),ray.direction,this.sizes,this.hover,['slice','cells'].includes(this.stage)?new Set(this.row):null);this.setHover(hit?.index??null);});
  this.listen(canvas,'pointerleave',e=>{if(canvas.parentElement.contains(e.relatedTarget))return;if(!this.vertex)this.setHover(null);});
  this.listen(canvas,'dblclick',e=>this.toggleAt(e),true);
  this.listen(canvas,'pointerdown',e=>{if(e.button===0&&['slice','cells'].includes(this.stage)&&this.enabled){e.preventDefault();e.stopImmediatePropagation();if(this.stage==='slice'){this.stage='cells';this.single=null;this.svgKey=null;this.emit();}else{this.single=this.hover;this.emit();}return;}if(e.button===2&&this.stage!=='vertex'&&this.enabled){e.preventDefault();e.stopImmediatePropagation();this.back();}},true);
  this.listen(canvas,'contextmenu',e=>{if(this.area?.enabled)e.preventDefault();});
  this.listen(this.svg,'contextmenu',e=>e.preventDefault());
  this.listen(this.svg,'pointerdown',e=>{e.preventDefault();e.stopPropagation();if(e.button===2){this.back();return;}if(e.button!==0)return;const t=e.target.closest('[data-corner],[data-axis]')||e.target;clearTimeout(this.clickTimer);if(t.dataset.corner!==undefined){this.clickTimer=setTimeout(()=>{this.single=null;this.vertex={index:Number(t.dataset.index),corner:Number(t.dataset.corner)};this.stage='axis';this.axis=null;this.row=[];this.svgKey=null;this.emit();},320);}else if(t.dataset.axis!==undefined){this.selectAxis(Number(t.dataset.axis),e);}});
  this.listen(this.svg,'keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.dataset.axis!==undefined){e.preventDefault();this.selectAxis(Number(e.target.dataset.axis));}});
  this.listen(this.svg,'dblclick',e=>this.toggleAt(e));
 }
 toggleAt(e){if(!this.area?.enabled||!this.enabled)return;clearTimeout(this.clickTimer);const r=this.renderer.cad.getBoundingClientRect(),ray=this.renderer.canvasRay(e.clientX-r.left,e.clientY-r.top);if(!ray)return;const hit=pickVoxel(this.area,ray.origin.map(v=>v/this.scale),ray.direction,this.sizes,this.hover,['slice','cells'].includes(this.stage)?new Set(this.row):null),index=hit?.index??(e.target.dataset.corner!==undefined?Number(e.target.dataset.index):null);if(index===null)return;e.preventDefault();e.stopImmediatePropagation();if(!['slice','cells'].includes(this.stage)){this.vertex=null;this.row=[];this.axis=null;this.stage='vertex';}this.single=index;this.setHover(index);this.setMode('edit');this.edit([index],null);}
 selectAxis(axis,e){if(this.stage==='cells')return;if(this.stage==='slice'){this.stage='cells';this.single=null;this.emit();return;}this.axis=axis;this.stage='slice';this.layer=cellCoordinates(this.area,this.vertex.index)[axis];this.sliceOrigin=e?[e.clientX,e.clientY]:null;this.sliceStart=this.layer;this.setSlice(this.layer);this.svgKey=null;}
 setSlice(layer){this.single=null;const max=gridShape(this.area)[this.axis]-1;this.layer=Math.max(0,Math.min(max,Math.round(layer)));this.row=sliceIndices(this.area,this.axis,this.layer);this.controls.querySelector('input').max=max;this.controls.querySelector('input').value=this.layer;this.controls.querySelector('span').textContent=`${'XYZ'[this.axis]} · slice ${this.layer+1} / ${max+1}`;this.svgKey=null;this.emit();}
 moveSlice(e){
  if(!this.sliceOrigin){this.sliceOrigin=[e.clientX,e.clientY];this.sliceStart=this.layer;return;}
  const c=cellCentre(this.area,this.vertex.index),next=[...c],yaw=this.area.yaw*Math.PI/180,step=this.area.cell_size;
  if(this.axis===0){next[0]+=Math.cos(yaw)*step;next[1]+=Math.sin(yaw)*step;}else if(this.axis===1){next[0]-=Math.sin(yaw)*step;next[1]+=Math.cos(yaw)*step;}else next[2]+=step;
  const p=this.renderer.project(c.map(v=>v*this.scale)),q=this.renderer.project(next.map(v=>v*this.scale));if(!p||!q)return;
  let dx=q[0]-p[0],dy=q[1]-p[1],length=Math.hypot(dx,dy);if(length<2){dx=0;dy=-1;length=1;}
  const distance=((e.clientX-this.sliceOrigin[0])*dx+(e.clientY-this.sliceOrigin[1])*dy)/length,layer=this.sliceStart+Math.round(distance/Math.max(14,length));if(layer!==this.layer)this.setSlice(layer);
 }
 setHover(index){if(index!==this.hover){this.hover=index;this.svgKey=null;this.emit();}}
 setMode(mode){this.mode=mode;this.renderer.onVoxelMode?.(mode);this.emit();}
 setEnabled(enabled){this.enabled=enabled;if(!enabled){this.hover=null;this.back(true);}this.svg.style.display=enabled?'':'none';this.controls.hidden=!enabled||!['slice','cells'].includes(this.stage);}
 setArea(area,alignment,snapshot){
  const key=area?.enabled?JSON.stringify([area.centre,area.width,area.depth,area.height,area.base,area.yaw,area.cell_size]):null;
  if(key!==this.geometry){clearTimeout(this.clickTimer);this.single=null;this.generation=(this.generation||0)+1;this.geometry=key;this.area=area?structuredClone(area):null;this.count=area?.enabled?gridShape(area).reduce((a,b)=>a*b,1):0;this.centres=Array.from({length:this.count},(_,i)=>cellCentre(area,i));const oldSizes=this.sizes;this.sizes=new Float32Array(this.count).fill(.44);if(oldSizes&&this.reveal)for(let i=0;i<Math.min(oldSizes.length,this.count);i++)this.sizes[i]=oldSizes[i];this.velocity=new Float32Array(this.count);this.extents=Array.from({length:this.count},(_,i)=>cellCoordinates(area,i).map((v,k)=>Math.min(area.cell_size,[area.width,area.depth,area.height][k]-v*area.cell_size)));this.instances=new Float32Array(this.count*11);this.manual=new Set();this.policyRevision=-1;this.observedAt=0;this.hover=null;this.vertex=null;this.row=[];this.stage='vertex';this.axis=null;this.cameraKey=null;this.svgKey=null;this.emit();}
  else this.area=area?structuredClone(area):null;
  this.scale=this.renderer.manifest?.calibration.meters_per_map_unit||1;
  if(snapshot){this.snapshot=snapshot;if((snapshot.policy_revision??0)>=this.policyRevision){this.policyRevision=snapshot.policy_revision??0;this.manual=new Set(snapshot.manual_blocked||[]);}if((snapshot.generated_at_ms??0)>=this.observedAt){this.observedAt=snapshot.generated_at_ms??0;this.observed=snapshot.observed_states||snapshot.states||[];}}else{this.snapshot=null;this.observed=[];}this.updateLabel();
 }
 back(all=false){clearTimeout(this.clickTimer);if(all)this.single=null;if(!all&&this.stage==='cells'){this.stage='slice';this.single=null;this.sliceOrigin=null;}else if(all||this.stage==='axis'){this.stage='vertex';this.vertex=null;this.row=[];this.axis=null;}else if(this.stage==='slice'){this.stage='axis';this.row=[];this.axis=null;}this.svgKey=null;this.emit();}
 updateLabel(){if(!this.label)return;const index=this.single??this.hover??this.vertex?.index,sensor=index===null||index===undefined?'':` · voxel ${index} · manual ${this.manual.has(index)?'blocked':'unblocked'} · live ${this.observed?.[index]||'unknown'}`;this.label.hidden=!this.area?.enabled||this.renderer.mode!=='cad';this.label.textContent=this.errorUntil>performance.now()?this.lastError:(this.mode==='edit'?'LAYOUT EDIT · green = manually unblocked, not live clearance':'LIVE STATUS · unknown cells remain unsafe')+sensor+(['slice','cells'].includes(this.stage)?` · ${'XYZ'[this.axis]} slice ${this.layer+1}: ${this.row.length} voxels · ${this.stage==='cells'?'LAYER FROZEN · select cells; Back to move layers':'move mouse along axis · click to freeze'}`:this.stage==='axis'?' · choose X, Y or Z':index!==null&&index!==undefined?' · click a vertex':'');}
 emit(){this.updateLabel();this.renderer.onVoxelSelection?.({stage:this.stage,voxel:this.single??this.hover??this.vertex?.index??null,axis:this.axis,row:[...this.row],layer:this.layer,selectedIndices:this.stage==='cells'&&this.single!==null?[this.single]:[...this.row],mode:this.mode});}
 queueEdit(action){const generation=this.generation,task=this.pending.then(()=>{if(generation!==this.generation)throw Error('Working area changed; select the voxel again');return action();});this.pending=task.catch(e=>{this.lastError=e.message;this.errorUntil=performance.now()+3500;this.updateLabel();this.renderer.onVoxelError?.(e.message);});return task;}
 edit(indices,blocked){if(!this.renderer.onVoxelEdit||!indices.length)return;const action=this.renderer.onVoxelEdit;return this.queueEdit(()=>action(indices,blocked));}
 clearManual(){const action=this.renderer.onVoxelClear;if(!action)return Promise.resolve();this.back(true);return this.queueEdit(action);}
 canonicalCorners(index){return scaledCorners(this.area,index,this.sizes[index]||.44);}
 drawHandles(){
  this.updateLabel();const r=this.renderer;this.controls.hidden=!['slice','cells'].includes(this.stage)||!this.area?.enabled||!this.enabled||r.mode!=='cad';const frozen=this.stage==='cells';this.controls.querySelector('input').disabled=frozen;for(const button of [...this.controls.querySelectorAll('button')].slice(0,2))button.disabled=frozen;if(!this.area?.enabled||!this.enabled||r.mode!=='cad'){this.svg.replaceChildren();this.svgKey=null;return;}
  const index=this.vertex?.index??this.hover??this.single;if(index===null||index===undefined){this.svg.replaceChildren();this.svgKey=null;return;}
  const key=[this.stage,index,this.vertex?.corner,this.axis].join('/');if(key!==this.svgKey){this.svgKey=key;this.svg.replaceChildren();
   if(this.stage==='vertex')for(let i=0;i<8;i++){const circle=svgNode('circle',{'r':7,'class':'voxel-vertex','data-corner':i,'data-index':index,'aria-label':`Voxel ${index} vertex ${i+1}`});this.svg.append(circle);}
   else{const origin=this.vertex.corner;for(const edge of EDGE_PAIRS.filter(e=>e.includes(origin)&&(!['slice','cells'].includes(this.stage)||edgeAxis(...e)===this.axis))){const next=edge.find(i=>i!==origin),axis=edgeAxis(origin,next),group=svgNode('g',{'data-axis':axis});const line=svgNode('line',{'class':'voxel-axis','data-axis':axis,'data-index':index,'data-next':next,'aria-label':`Select ${'XYZ'[axis]} voxel slice`});line.style.stroke=['#fc7a74','#b7f48a','#82bfff'][axis];const target=svgNode('rect',{'class':'voxel-axis-button','width':32,'height':28,'rx':8,'data-axis':axis,'role':'button','tabindex':0,'aria-label':`Select ${'XYZ'[axis]} voxel slices`});const text=svgNode('text',{'data-axis':axis,'data-index':index,'data-next':next});text.textContent='XYZ'[axis];const hit=line.cloneNode();hit.setAttribute('class','voxel-axis-hit');hit.style.stroke='transparent';hit.style.strokeOpacity='0';group.append(line,hit,target,text);this.svg.append(group);}}
  }
  this.svg.setAttribute('viewBox',`0 0 ${r.cad.clientWidth} ${r.cad.clientHeight}`);const world=this.canonicalCorners(index),points=world.map(p=>r.project(p.map(v=>v*this.scale)));
  if(this.stage==='vertex'){for(const dot of this.svg.children){const p=points[Number(dot.dataset.corner)];dot.style.display=p?'':'none';if(p){dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);}}}
  else{const start=world[this.vertex.corner],p=r.project(start.map(v=>v*this.scale));for(const group of this.svg.children){const line=group.querySelector('line'),text=group.querySelector('text'),next=world[Number(line.dataset.next)],end=start.map((v,i)=>v+(next[i]-v)*2.8),q=r.project(end.map(v=>v*this.scale));group.style.display=p&&q?'':'none';if(p&&q){for(const l of group.querySelectorAll('line'))for(const [k,v] of Object.entries({x1:p[0],y1:p[1],x2:q[0],y2:q[1]}))l.setAttribute(k,v);const button=group.querySelector('rect');button.setAttribute('x',q[0]-16);button.setAttribute('y',q[1]-14);text.setAttribute('x',q[0]);text.setAttribute('y',q[1]+4);}}}
 }
 draw(camera,canvas,time){
  if(!this.area?.enabled||!this.count){this.drawHandles();return;}const gl=this.gl,dt=Math.min(.034,(time-(this.lastTime||time-16))/1000);this.lastTime=time;
  const centreRay=this.renderer.canvasRay(this.renderer.cad.clientWidth/2,this.renderer.cad.clientHeight/2),cameraOrigin=centreRay?.origin.map(v=>v/this.scale);
  const focus=this.hover!==null?this.centres[this.hover]:this.vertex?this.centres[this.vertex.index]:null,selected=new Set(this.row),camKey=JSON.stringify([camera.rotation,camera.translation]);
  if(camKey!==this.cameraKey){this.cameraKey=camKey;this.order=Array.from({length:this.count},(_,i)=>i).sort((a,b)=>{const z=p=>camera.rotation[2].reduce((s,x,i)=>s+x*p[i]*this.scale,camera.translation[2]);return z(this.centres[b])-z(this.centres[a]);});}
  for(let slot=0;slot<this.count;slot++){
   const index=this.order[slot],centre=this.centres[index],d=focus?Math.hypot(...centre.map((v,i)=>(v-focus[i])/this.area.cell_size)):100,near=Math.exp(-d*d/3.5),reveal=Math.max(this.reveal,this.reduced?0:Math.max(0,1-Math.max(0,time-(this.cameraRevealUntil||0))/400)),chosen=selected.has(index)||index===this.single,target=.44+.44*Math.max(near,reveal,chosen?.8:0);[this.sizes[index],this.velocity[index]]=springStep(this.sizes[index],this.velocity[index],target,dt,this.reduced);
   const observed=this.observed?.[index]||'unknown',blocked=this.manual.has(index),unsafe=observed==='occupied'||blocked;
   let colour=unsafe?[1,.27,.23]:this.mode==='edit'?[.48,.90,.66]:observed==='free'?[.38,.87,.52]:[.76,.66,.42];
   if(chosen&&!unsafe)colour=colour.map((v,i)=>v*.5+[.55,.83,1][i]*.5);
   const visible=!['slice','cells'].includes(this.stage)||!cameraOrigin||sliceVisible(this.area,index,this.axis,this.layer,cameraOrigin);const alpha=visible?(index===this.single?.7:unsafe?.45:chosen?.42:.055+.46*Math.max(near,reveal)):0;
   this.instances.set([...centre.map(v=>v*this.scale),...colour,this.sizes[index],alpha,...this.extents[index].map(v=>v*this.scale)],slot*11);
  }
  gl.useProgram(this.program);gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.instanceBuffer);gl.bufferData(gl.ARRAY_BUFFER,this.instances,gl.DYNAMIC_DRAW);
  for(const [location,size,offset] of [[2,3,0],[3,3,12],[4,2,24],[5,3,32]]){gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,gl.FLOAT,false,44,offset);gl.vertexAttribDivisor(location,1);}
  gl.uniformMatrix3fv(this.uniforms.cameraRotation,false,transpose3(camera.rotation));gl.uniform3fv(this.uniforms.translation,camera.translation);gl.uniform2f(this.uniforms.viewport,canvas.width,canvas.height);const fit=Math.min(canvas.width/camera.width,canvas.height/camera.height);gl.uniform4f(this.uniforms.intrinsics,camera.K[0][0]*fit,camera.K[1][1]*fit,camera.K[0][2]*fit+(canvas.width-camera.width*fit)/2,camera.K[1][2]*fit+(canvas.height-camera.height*fit)/2);const yaw=this.area.yaw*Math.PI/180;gl.uniform2f(this.uniforms.yaw,Math.cos(yaw),Math.sin(yaw));gl.uniform1f(this.uniforms.cellSize,this.area.cell_size*this.scale);
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);gl.enable(gl.DEPTH_TEST);
  for(const [buffer,mode,count,edges] of [[this.faceBuffer,gl.TRIANGLES,36,0],[this.edgeBuffer,gl.LINES,24,1]]){gl.bindBuffer(gl.ARRAY_BUFFER,buffer);for(let i=0;i<2;i++){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,3,gl.FLOAT,false,24,i*12);gl.vertexAttribDivisor(i,0);}gl.uniform1f(this.uniforms.edges,edges);gl.drawArraysInstanced(mode,0,count,this.count);}
  gl.depthMask(true);gl.disable(gl.BLEND);gl.bindVertexArray(null);this.drawHandles();
 }
 dispose(){clearTimeout(this.clickTimer);for(const remove of this.listeners)remove();this.svg.remove();this.label.remove();this.controls.remove();const gl=this.gl;for(const b of [this.faceBuffer,this.edgeBuffer,this.instanceBuffer])gl.deleteBuffer(b);gl.deleteVertexArray(this.vao);gl.deleteProgram(this.program);}
}
