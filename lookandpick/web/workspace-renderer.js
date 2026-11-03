import {GaussianViewer} from '/workspace-viewer.js';
import {I,mul,point,direction,xyz,axisRotation,gaussianFrame,transformGaussians,rectangle,dot,transpose} from './workspace-math.js';

import {remainingFreshness} from './workspace-voxels.js';

import {VoxelCubes} from './voxel-cubes.js';

const vertex=`#version 300 es
precision highp float;layout(location=0) in vec3 position;layout(location=1) in vec3 colour;layout(location=2) in vec3 normal;
uniform mat4 model;uniform mat3 cameraRotation;uniform vec3 translation;uniform vec4 intrinsics;uniform vec2 viewport;uniform float points;
out vec3 rgb;void main(){vec3 world=(model*vec4(position,1.)).xyz;vec3 p=cameraRotation*world+translation;
vec2 px=intrinsics.xy*p.xy/max(p.z,.00001)+intrinsics.zw;float near=.01,far=100.;
gl_Position=vec4((2.*px.x/viewport.x-1.)*p.z,(1.-2.*px.y/viewport.y)*p.z,((far+near)/(far-near))*p.z-2.*far*near/(far-near),p.z);
gl_PointSize=2.;vec3 n=normalize(mat3(model)*normal);float light=points>0.?1.:.45+.55*abs(dot(n,normalize(vec3(.4,-.6,1.))));rgb=colour*light;}`;
const fragment=`#version 300 es
precision highp float;in vec3 rgb;out vec4 result;void main(){result=vec4(rgb,1.);}`;
const flatten=m=>new Float32Array(transpose(m).flat());
function triangles(vertices,faces,colors){
 const output=[];for(const f of faces){const [a,b,c]=f.map(i=>vertices[i]),u=b.map((x,i)=>x-a[i]),v=c.map((x,i)=>x-a[i]),n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];for(const i of f)output.push(...vertices[i],...(Array.isArray(colors[0])?colors[i]:colors),...n);}return new Float32Array(output);
}
function stl(raw,color){const view=new DataView(raw),count=view.getUint32(80,true);if(84+count*50!==raw.byteLength)throw Error('Robot asset is not a binary STL');const out=new Float32Array(count*27);for(let i=0;i<count;i++){const p=84+i*50,n=[0,1,2].map(k=>view.getFloat32(p+k*4,true));for(let v=0;v<3;v++)out.set([...Array.from({length:3},(_,k)=>view.getFloat32(p+12+v*12+k*4,true)),...color,...n],i*27+v*9);}return out;}
function glb(raw){
 const view=new DataView(raw);if(view.getUint32(0,true)!==0x46546c67)throw Error('Invalid surface GLB');const length=view.getUint32(12,true),json=JSON.parse(new TextDecoder().decode(new Uint8Array(raw,20,length))),start=20+length+8;
 function accessor(id){const a=json.accessors[id],b=json.bufferViews[a.bufferView],size={SCALAR:1,VEC2:2,VEC3:3,VEC4:4}[a.type],bytes={5121:1,5123:2,5125:4,5126:4}[a.componentType],read={5121:'getUint8',5123:'getUint16',5125:'getUint32',5126:'getFloat32'}[a.componentType];return Array.from({length:a.count},(_,i)=>Array.from({length:size},(_,k)=>{let x=view[read](start+(b.byteOffset||0)+(a.byteOffset||0)+i*(b.byteStride||size*bytes)+k*bytes,true);if(a.normalized)x/=a.componentType===5121?255:65535;return x;}));}
 const out=[];for(const mesh of json.meshes)for(const p of mesh.primitives){const vertices=accessor(p.attributes.POSITION),indices=p.indices===undefined?vertices.map((_,i)=>i):accessor(p.indices).flat(),colors=p.attributes.COLOR_0===undefined?[.53,.58,.48]:accessor(p.attributes.COLOR_0).map(c=>c.slice(0,3));const faces=[];for(let i=0;i<indices.length;i+=3)faces.push(indices.slice(i,i+3));out.push(triangles(vertices,faces,colors));}const size=out.reduce((s,a)=>s+a.length,0),result=new Float32Array(size);let offset=0;for(const a of out){result.set(a,offset);offset+=a.length;}return result;
}

export class WorkspaceRenderer{
 constructor(photo,cad,onStatus=()=>{}){
  this.photo=new GaussianViewer(photo,{onStatus});this.cad=cad;this.gl=cad.getContext('webgl2',{antialias:true});if(!this.gl)throw Error('WebGL 2 is required');const gl=this.gl;
  const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  this.program=gl.createProgram();gl.attachShader(this.program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(this.program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  this.uniforms=Object.fromEntries(['model','cameraRotation','translation','intrinsics','viewport','points'].map(k=>[k,gl.getUniformLocation(this.program,k)]));this.buffers=[];this.robot=[];this.mode='cad';this.generation=0;
  let drag=null;cad.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,button:e.button};cad.setPointerCapture(e.pointerId);});cad.addEventListener('pointermove',e=>{if(!drag||!this.photo.camera)return;const x=e.clientX-drag.x,y=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.button===0&&y<0)this.cubes.cameraRevealUntil=performance.now()+350;if(drag.button===2||e.shiftKey)this.photo.pan(x,y);else this.photo.orbit(x,y);});for(const event of ['pointerup','pointercancel'])cad.addEventListener(event,()=>drag=null);cad.addEventListener('contextmenu',e=>e.preventDefault());cad.addEventListener('wheel',e=>{e.preventDefault();photo.dispatchEvent(new WheelEvent('wheel',{deltaY:e.deltaY,cancelable:true}));},{passive:false});
  this.cubes=new VoxelCubes(this);
  const draw=()=>{this.render();this.animation=requestAnimationFrame(draw);};draw();
 }
 buffer(values,mode){const gl=this.gl,b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);const entry={buffer:b,count:values.length/9,mode:mode||gl.TRIANGLES};this.buffers.push(entry);return entry;}
 release(){clearTimeout(this.voxelTimeout);this.cubes?.setArea(null,null,null);for(const b of this.buffers)this.gl.deleteBuffer(b.buffer);this.buffers=[];this.robot=[];this.surface=null;this.observed=null;this.proxies=[];this.sourceData=null;this.sourceMetadata=null;this.voxelBuffer=null;this.voxelSignature=null;}
 async load(manifest,base){
  const generation=++this.generation;this.release();this.photo.clearScene();this.manifest=null;
  const request=async path=>{const r=await fetch(path);if(!r.ok)throw Error('Workspace visual asset unavailable');return r;};
  const [scene,mesh,objects,description]=await Promise.all([this.photo.load(base),request(base+'/surfaces.glb').then(r=>r.arrayBuffer()),request(base+'/observed.json').then(r=>r.json()),request('/api/robot-description').then(r=>r.json())]);
  if(generation!==this.generation||!scene)return null;
  this.sourceData=this.photo.data.slice();this.sourceMetadata=structuredClone(this.photo.metadata);this.sourceTarget=[...this.photo.target];this.frameSignature=JSON.stringify(manifest.frames.workspace_from_map);this.manifest=structuredClone(manifest);this.frame=gaussianFrame(manifest);transformGaussians(this.photo,manifest);this.surface=this.buffer(glb(mesh));
  const values=[];for(let i=0;i<objects.points.length;i++)values.push(...objects.points[i],...objects.colors[i],0,0,1);this.observed=this.buffer(new Float32Array(values),this.gl.POINTS);this.description=description;
  const assets=await Promise.all(description.visuals.map(async visual=>({visual,raw:await request('/api/robot-assets/'+visual.file).then(r=>r.arrayBuffer())})));
  if(generation!==this.generation)return null;
  this.robot=assets.map(({visual,raw})=>({...visual,geometry:this.buffer(stl(raw,visual.color))}));this.update(manifest);this.photo.capturedView(Math.min(2,scene.cameras.length-1));return scene;
 }
 update(manifest){
  this.manifest=structuredClone(manifest);if(!this.description)return;
  const signature=JSON.stringify(manifest.frames.workspace_from_map);
  if(this.sourceData&&signature!==this.frameSignature){this.frameSignature=signature;this.photo.data.set(this.sourceData);this.photo.metadata=structuredClone(this.sourceMetadata);this.photo.target=[...this.sourceTarget];transformGaussians(this.photo,manifest);this.photo.capturedView(0);}
  this.photo.canvas.style.visibility=manifest.layers.background?'visible':'hidden';
  // Regenerate only the cheap editable surface proxies; inference assets stay untouched.
  for(const p of this.proxies||[]){this.gl.deleteBuffer(p.geometry.buffer);this.buffers=this.buffers.filter(b=>b!==p.geometry);}
  const scale=manifest.calibration.meters_per_map_unit||1;this.proxies=manifest.surfaces.filter(s=>s.enabled).map(s=>({name:s.name,geometry:this.buffer(triangles(rectangle(s,scale),[[0,1,2],[0,2,3]],s.name==='table'?[.61,.47,.3]:[.65,.66,.6]))}));
 }
 setWorkingArea(area,alignment,snapshot){
  clearTimeout(this.voxelTimeout);
  if(snapshot?.states?.some(s=>s!=='unknown')){const remaining=remainingFreshness(snapshot,area.ttl_ms);this.voxelTimeout=setTimeout(()=>this.setWorkingArea(area,alignment,null),remaining);}
  this.cubes.setArea(area,alignment,snapshot);
  const signature=JSON.stringify([area,alignment,this.manifest?.calibration.meters_per_map_unit]);if(signature===this.voxelSignature)return;this.voxelSignature=signature;
  if(this.voxelBuffer){this.gl.deleteBuffer(this.voxelBuffer.buffer);this.buffers=this.buffers.filter(b=>b!==this.voxelBuffer);this.voxelBuffer=null;}
  if(area?.enabled){
   const scale=this.manifest?.calibration.meters_per_map_unit||1,extra=[];
   if(alignment){const origin=point(alignment.canonical_from_marker,[0,0,.003]).map(v=>v*scale);for(let axis=0;axis<3;axis++){const p=[0,0,.003];p[axis]+=.12;const end=point(alignment.canonical_from_marker,p).map(v=>v*scale),colour=[[1,.3,.25],[.3,1,.35],[.35,.6,1]][axis];for(const value of [origin,end])extra.push(...value,...colour,0,0,1);}}
   const values=new Float32Array(extra);this.voxelBuffer=this.buffer(values,this.gl.LINES);
  }
 }
 setMode(mode){this.mode=mode;this.cubes.drawHandles();this.photo.canvas.style.opacity=mode==='photo'?'1':'0';this.photo.canvas.style.pointerEvents=mode==='photo'?'auto':'none';this.cad.style.opacity=mode==='cad'?'1':'0';this.cad.style.pointerEvents=mode==='cad'?'auto':'none';}
 canvasRay(x,y){const c=this.photo.camera;if(!c)return null;const fit=Math.min(this.cad.clientWidth/c.width,this.cad.clientHeight/c.height),u=(x-(this.cad.clientWidth-c.width*fit)/2)/fit,v=(y-(this.cad.clientHeight-c.height*fit)/2)/fit;return {origin:this.photo.centre(),direction:transpose(c.rotation).map(row=>dot(row,[(u-c.K[0][2])/c.K[0][0],(v-c.K[1][2])/c.K[1][1],1]))};}
 project(p){const c=this.photo.camera;if(!c)return null;const t=c.translation,r=c.rotation,cam=r.map((row,i)=>dot(row,p)+t[i]);if(cam[2]<=0)return null;const w=this.cad.clientWidth,h=this.cad.clientHeight,s=Math.min(w/c.width,h/c.height);return [(c.K[0][0]*cam[0]/cam[2]+c.K[0][2])*s+(w-c.width*s)/2,(c.K[1][1]*cam[1]/cam[2]+c.K[1][2])*s+(h-c.height*s)/2];}
 planePoint(x,y,height){const c=this.photo.camera;if(!c)return null;const s=Math.min(this.cad.clientWidth/c.width,this.cad.clientHeight/c.height),u=(x-(this.cad.clientWidth-c.width*s)/2)/s,v=(y-(this.cad.clientHeight-c.height*s)/2)/s,ray=transpose(c.rotation).map(row=>dot(row,[(u-c.K[0][2])/c.K[0][0],(v-c.K[1][2])/c.K[1][1],1])),centre=this.photo.centre();if(Math.abs(ray[2])<1e-7)return null;const k=(height-centre[2])/ray[2];return k>0?centre.map((x,i)=>x+k*ray[i]):null;}
 render(){
  const gl=this.gl,canvas=this.cad,c=this.photo.camera;if(!canvas.clientWidth||!canvas.clientHeight)return;const ratio=Math.min(devicePixelRatio||1,2),width=Math.max(1,Math.round(canvas.clientWidth*ratio)),height=Math.max(1,Math.round(canvas.clientHeight*ratio));if(canvas.width!==width)canvas.width=width;if(canvas.height!==height)canvas.height=height;gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.075,.085,.08,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(!c||!this.manifest||this.mode!=='cad')return;
  gl.useProgram(this.program);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LESS);gl.disable(gl.CULL_FACE);gl.uniformMatrix3fv(this.uniforms.cameraRotation,false,flatten(c.rotation));gl.uniform3fv(this.uniforms.translation,c.translation);gl.uniform2f(this.uniforms.viewport,canvas.width,canvas.height);const s=Math.min(canvas.width/c.width,canvas.height/c.height);gl.uniform4f(this.uniforms.intrinsics,c.K[0][0]*s,c.K[1][1]*s,c.K[0][2]*s+(canvas.width-c.width*s)/2,c.K[1][2]*s+(canvas.height-c.height*s)/2);
  const draw=(entry,model)=>{if(!entry)return;gl.bindBuffer(gl.ARRAY_BUFFER,entry.buffer);for(let i=0;i<3;i++){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,3,gl.FLOAT,false,36,i*12);}gl.uniformMatrix4fv(this.uniforms.model,false,flatten(model));gl.uniform1f(this.uniforms.points,entry.mode===gl.POINTS?1:0);gl.drawArrays(entry.mode,0,entry.count);};
  const m=this.manifest,t=m.frames.workspace_from_map,scale=m.calibration.meters_per_map_unit||1;
  if(m.layers.surfaces){if(m.needs_rebuild){for(const p of this.proxies)draw(p.geometry,I());}else draw(this.surface,t);}
  if(m.layers.observed)draw(this.observed,t);
  if(this.voxelBuffer)draw(this.voxelBuffer,I());
  if(m.layers.robot){const r=m.robot,base=xyz(r.translation.map(x=>x*scale),r.rotation.map(x=>x*Math.PI/180),r.scale*scale),poses={[this.description.base_link]:base},pending=[...this.description.joints];
   while(pending.length){let progressed=false;for(const joint of [...pending]){if(!poses[joint.parent])continue;let local=xyz(joint.origin.xyz,joint.origin.rpy),motion=I();if(['revolute','continuous'].includes(joint.type))motion=axisRotation(joint.axis,r.joint_positions[joint.name]||0);poses[joint.child]=mul(poses[joint.parent],mul(local,motion));pending.splice(pending.indexOf(joint),1);progressed=true;}if(!progressed)break;}
   for(const part of this.robot){const visual=xyz(part.origin.xyz,part.origin.rpy),size=I();part.scale.forEach((x,i)=>size[i][i]=x);if(poses[part.link])draw(part.geometry,mul(poses[part.link],mul(visual,size)));}}
  this.cubes.draw(c,canvas,performance.now());
  this.onFrame?.();
 }
 dispose(){++this.generation;this.release();this.cubes.dispose();cancelAnimationFrame(this.animation);cancelAnimationFrame(this.photo.animation);this.photo.observer.disconnect();this.photo.clearScene();this.gl.deleteProgram(this.program);}
}
