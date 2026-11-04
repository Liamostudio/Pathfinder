import {runRobot,runItems,filmCamera,boxMesh,sphereMesh,ringMesh,modelAt,dashedRoute,remainingRoute} from './run-scene.js';
import {JointEase} from './live-arm-view.js';
import {GaussianViewer} from '/workspace-viewer.js';
import {I,mul,point,direction,xyz,axisRotation,gaussianFrame,transformGaussians,rectangle,dot,transpose} from './workspace-math.js';

import {remainingFreshness} from './workspace-voxels.js';

import {VoxelCubes} from './voxel-cubes.js';
import {routeVertices,routeFrame} from './motion-view.js';

const vertex=`#version 300 es
precision highp float;layout(location=0) in vec3 position;layout(location=1) in vec3 colour;layout(location=2) in vec3 normal;layout(location=3) in vec2 uv;
uniform mat4 model;uniform mat3 cameraRotation;uniform vec3 translation;uniform vec4 intrinsics;uniform vec2 viewport;uniform float points;
out vec3 rgb;out vec2 texUV;out float facing;void main(){vec3 world=(model*vec4(position,1.)).xyz;vec3 p=cameraRotation*world+translation;
vec2 px=intrinsics.xy*p.xy/max(p.z,.00001)+intrinsics.zw;float near=.01,far=100.;
gl_Position=vec4((2.*px.x/viewport.x-1.)*p.z,(1.-2.*px.y/viewport.y)*p.z,((far+near)/(far-near))*p.z-2.*far*near/(far-near),p.z);
gl_PointSize=points>1.?points:2.;vec3 n=normalize(mat3(model)*normal);float light=points>0.?1.:.65+.35*abs(dot(n,normalize(vec3(.4,-.6,1.))));rgb=colour*light;texUV=uv;facing=abs(dot(normalize(cameraRotation*n),normalize(-p)));}`;
const fragment=`#version 300 es
precision highp float;in vec3 rgb;in vec2 texUV;in float facing;uniform bool softEdges;uniform sampler2D appearance;uniform bool textured;uniform float opacity;uniform vec3 tint;out vec4 result;void main(){vec4 t=textured?texture(appearance,texUV):vec4(rgb,1.);result=vec4(textured?mix(vec3(.61,.47,.3),t.rgb,t.a):rgb*tint,opacity*(softEdges?smoothstep(0.,.35,facing):1.));}`;
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
  this.photo=new GaussianViewer(photo,{onStatus});this.cad=cad;this.gl=cad.getContext('webgl2',{antialias:true,alpha:true});if(!this.gl)throw Error('WebGL 2 is required');const gl=this.gl;this.listeners=[];const listen=(target,event,fn,options)=>{target.addEventListener(event,fn,options);this.listeners.push(()=>target.removeEventListener(event,fn,options));};
  const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  this.program=gl.createProgram();gl.attachShader(this.program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(this.program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  this.uniforms=Object.fromEntries(['model','cameraRotation','translation','intrinsics','viewport','points','textured','appearance','opacity','tint','softEdges'].map(k=>[k,gl.getUniformLocation(this.program,k)]));this.buffers=[];this.robot=[];this.mode='cad';this.generation=0;
  let drag=null;listen(cad,'pointerdown',e=>{drag={x:e.clientX,y:e.clientY,button:e.button};cad.setPointerCapture(e.pointerId);});listen(cad,'pointermove',e=>{if(!drag||!this.photo.camera)return;const x=e.clientX-drag.x,y=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.button===0&&y<0)this.cubes.cameraRevealUntil=performance.now()+350;if(drag.button===2||e.shiftKey)this.photo.pan(x,y);else this.photo.orbit(x,y);});for(const event of ['pointerup','pointercancel'])listen(cad,event,()=>drag=null);listen(cad,'contextmenu',e=>e.preventDefault());listen(cad,'wheel',e=>{e.preventDefault();photo.dispatchEvent(new WheelEvent('wheel',{deltaY:e.deltaY,cancelable:true}));},{passive:false});
  listen(cad,'webglcontextlost',e=>{e.preventDefault();this.onFailure?.('3D graphics unavailable · schematic fallback; reload to retry');});
  listen(photo,'webglcontextlost',e=>{e.preventDefault();this.photoUnavailable=true;this.runPhoto=false;this.mode='cad';this.photo.canvas.style.opacity='0';this.cad.style.opacity='1';this.cad.style.pointerEvents='auto';this.onAppearance?.('Scene photo unavailable · CAD retained; reload to retry');});
  this.cubes=new VoxelCubes(this);
  const photoRender=this.photo.render.bind(this.photo);this.photo.render=()=>{if(this.mode==='photo'||this.runPhoto)photoRender();else{this.photo.dirty=false;const ratio=Math.min(devicePixelRatio||1,2);const width=Math.max(1,Math.round(this.cad.clientWidth*ratio)),height=Math.max(1,Math.round(this.cad.clientHeight*ratio));if(this.photo.canvas.width!==width)this.photo.canvas.width=width;if(this.photo.canvas.height!==height)this.photo.canvas.height=height;}};
  const draw=()=>{this.render();this.animation=requestAnimationFrame(draw);};draw();
 }
 buffer(values,mode){const gl=this.gl,b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);const entry={buffer:b,count:values.length/9,mode:mode||gl.TRIANGLES};this.buffers.push(entry);return entry;}
 release(){this.jointEase=null;this.runMeshes=null;this.trail=[];this.runEase=null;this.runFrame=null;if(this.tableTexture)this.gl.deleteTexture(this.tableTexture);this.tableTexture=null;this.tableQuad=null;this.photoReady=false;this.runPose=null;this.items=null;this.displayItems=null;this.itemsFrom=null;this.textureSignature=null;this.textureVersion=(this.textureVersion||0)+1;clearTimeout(this.voxelTimeout);this.cubes?.setArea(null,null,null);for(const b of this.buffers)this.gl.deleteBuffer(b.buffer);this.buffers=[];this.robot=[];this.surface=null;this.observed=null;this.proxies=[];this.sourceData=null;this.sourceMetadata=null;this.voxelBuffer=null;this.voxelSignature=null;this.routeBuffer=null;this.routeNodes=null;this.routeSignature=null;this.routeInput=null;this.routeStart=null;this.routeFrame=null;}
 async load(manifest,base){
  const generation=++this.generation;this.release();this.photo.clearScene();this.manifest=null;this.assetBase=base;
  const request=async path=>{const r=await fetch(path,{signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error('Workspace visual asset unavailable');return r;};
  const [scene,mesh,objects,description]=await Promise.all([request(base+'/scene.json').then(r=>r.json()),request(base+'/surfaces.glb').then(r=>r.arrayBuffer()).catch(()=>null),request(base+'/observed.json').then(r=>r.json()).catch(()=>({points:[],colors:[]})),request('/api/robot-description').then(r=>r.json())]);
  if(generation!==this.generation||!scene)return null;
  this.photo.metadata=structuredClone(scene);this.photo.data=new Float32Array(0);this.photo.indices=[];this.photo.sorted=new Float32Array(0);this.photo.count=0;this.photo.target=scene.bounds[0].map((v,i)=>(v+scene.bounds[1][i])/2);this.sourceData=this.photo.data.slice();this.sourceMetadata=structuredClone(this.photo.metadata);this.sourceTarget=[...this.photo.target];this.frameSignature=JSON.stringify(manifest.frames.workspace_from_map);this.manifest=structuredClone(manifest);this.frame=gaussianFrame(manifest);transformGaussians(this.photo,manifest);this.surface=mesh?this.buffer(glb(mesh)):null;
  const values=[];for(let i=0;i<objects.points.length;i++)values.push(...objects.points[i],...objects.colors[i],0,0,1);this.observed=this.buffer(new Float32Array(values),this.gl.POINTS);this.description=description;
  const assets=await Promise.all(description.visuals.map(async visual=>({visual,raw:await request('/api/robot-assets/'+visual.file).then(r=>r.arrayBuffer())})));
  if(generation!==this.generation)return null;
  this.robot=assets.map(({visual,raw})=>({...visual,geometry:this.buffer(stl(raw,visual.color))}));this.update(manifest);this.photo.capturedView(Math.min(2,scene.cameras.length-1));return scene;
 }
 update(manifest,{easeJoints=false}={}){
  const now=performance.now(),from=this.jointEase?.value(now)||this.manifest?.robot?.joint_positions||manifest.robot.joint_positions;
  this.jointEase=easeJoints?new JointEase(from,manifest.robot.joint_positions,now):null;
  this.manifest=structuredClone(manifest);if(!this.description)return;
  const signature=JSON.stringify(manifest.frames.workspace_from_map);
  if(this.sourceData&&signature!==this.frameSignature){this.frameSignature=signature;this.photo.data.set(this.sourceData);this.photo.metadata=structuredClone(this.sourceMetadata);this.photo.target=[...this.sourceTarget];transformGaussians(this.photo,manifest);this.photo.capturedView(0);}
  this.photo.canvas.style.visibility=manifest.layers.background?'visible':'hidden';
  const textureSignature=JSON.stringify([manifest.surfaces,manifest.marker_alignment,manifest.calibration.meters_per_map_unit]);
  if(textureSignature!==this.textureSignature){this.textureSignature=textureSignature;if(this.tableTexture)this.gl.deleteTexture(this.tableTexture);this.tableTexture=null;if(this.tableQuad){this.gl.deleteBuffer(this.tableQuad.buffer);this.buffers=this.buffers.filter(b=>b!==this.tableQuad);}this.tableQuad=null;this.loadTableTexture(this.assetBase,this.generation,++this.textureVersion);}
  // Regenerate only the cheap editable surface proxies; inference assets stay untouched.
  for(const p of this.proxies||[]){this.gl.deleteBuffer(p.geometry.buffer);this.buffers=this.buffers.filter(b=>b!==p.geometry);}
  const scale=manifest.calibration.meters_per_map_unit||1;this.proxies=manifest.surfaces.filter(s=>s.enabled).map(s=>({name:s.name,geometry:this.buffer(triangles(rectangle(s,scale),[[0,1,2],[0,2,3]],s.name==='table'?[.61,.47,.3]:[.65,.66,.6]))}));
 }
 holdJointPose(){if(this.jointEase&&this.manifest){this.manifest.robot.joint_positions=this.jointEase.value(performance.now(),matchMedia('(prefers-reduced-motion: reduce)').matches);this.jointEase=null;}}
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
 setRoute(route,start=null){
  this.routeInput=route;this.routeFrame=routeFrame(this.routeFrame,route,start);this.routeStart=this.routeFrame.origin;start=this.routeStart;if(!this.manifest)return;
  const scale=this.manifest.calibration.meters_per_map_unit||1,alignment=this.manifest.marker_alignment,signature=JSON.stringify([route,start,alignment?.canonical_from_marker,scale]);if(signature===this.routeSignature)return;this.routeSignature=signature;
  for(const entry of [this.routeBuffer,this.routeNodes].filter(Boolean)){this.gl.deleteBuffer(entry.buffer);this.buffers=this.buffers.filter(b=>b!==entry);}this.routeBuffer=null;this.routeNodes=null;
  const vertices=routeVertices(route,start,alignment,scale);this.plannedVertices=vertices;if(vertices.length){this.routeNodes=this.buffer(vertices,this.gl.POINTS);this.routeBuffer=this.buffer(dashedRoute(vertices),this.gl.LINES);}
 }
 async loadTableTexture(base,generation,version){
  const gl=this.gl;try{
   const root=base.replace(/\/assets$/,'');const response=await fetch(root+'/table-texture.json',{signal:AbortSignal.timeout(28000)});if(!response.ok)throw Error('No marker-plane texture');const meta=await response.json();
   const png=await fetch(root+'/table-texture.png',{signal:AbortSignal.timeout(10000)});if(!png.ok)throw Error('Table texture unavailable');const image=await createImageBitmap(await png.blob());if(this.generation!==generation||version!==this.textureVersion){image.close();return;}
   const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);image.close();gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
   this.tableTexture=texture;this.tableMeta=meta;const scale=this.manifest.calibration.meters_per_map_unit||1,values=[];
   for(const i of [0,1,2,0,2,3])values.push(...meta.corners[i].map(x=>x*scale),1,1,1,0,0,1,...meta.uv[i]);
   this.tableQuad=this.buffer(new Float32Array(values));this.tableQuad.count=6;this.tableQuad.textured=true;
   this.textureNote='Marker-plane tabletop · '+meta.source;this.onAppearance?.(this.textureNote);
  }catch{if(this.generation===generation&&version===this.textureVersion){this.textureNote=this.runMode?'Plain wood tabletop · marker-plane texture unavailable':'Projected tabletop fallback · marker-plane texture unavailable';this.onAppearance?.(this.textureNote);}}
 }
 setRunMode(enabled){this.runMode=enabled;this.cubes.setEnabled(!enabled);this.cubes.runStyle=enabled;this.runPhoto=false;this.setMode('cad');if(enabled)this.filmView();this.cubes.updateLabel();this.cubes.label.hidden=enabled;}
 tableTopView(){if(!this.manifest)return;const t=this.manifest.surfaces.find(s=>s.name==='table'),s=this.manifest.calibration.meters_per_map_unit||1,target=[t.centre[0]*s,t.centre[1]*s,t.height*s];this.photo.target=target;this.photo.up=[0,1,0];this.photo.setLookAt([target[0],target[1],target[2]+Math.max(t.width,t.depth)*s*1.2],target);}
 filmView(){if(!this.manifest)return;const view=filmCamera(this.manifest);this.photo.target=view.target;this.photo.up=[0,0,1];this.photo.setLookAt(view.eye,view.target);}
 // The same frame contract is used by live polling and a future replay player.
 renderFrame(frame){
  const reset=this.runFrame?.instance_id!==frame.instance_id||(Number.isFinite(frame.t)&&frame.t<(this.runFrame?.t??-1));if(reset){this.trail=[];this.runEase=null;}
  this.itemsFrom=this.displayItems||this.items;this.runFrame=frame;this.runAt=performance.now();const pose=runRobot(frame,this.manifest),from=this.runEase?.value(this.runAt)||this.runPose?.joints;this.runPose=pose;
  if(pose)this.runEase=new JointEase(from||pose.joints,pose.joints,this.runAt,110);
  this.items=runItems(frame,this.manifest);this.setRoute(frame.route,frame.scene?.arm_position);if(this.routeBuffer&&this.plannedVertices){const remaining=remainingRoute(this.plannedVertices,this.items.tcp),dashes=dashedRoute(remaining);for(const [entry,data] of [[this.routeBuffer,dashes],[this.routeNodes,remaining]]){this.gl.bindBuffer(this.gl.ARRAY_BUFFER,entry.buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,data,this.gl.DYNAMIC_DRAW);entry.count=data.length/9;}}
  if(this.items.tcp){this.trail??=[];const previous=this.trail.at(-1);if(!previous||Math.hypot(...this.items.tcp.map((v,i)=>v-previous.p[i]))>.001)this.trail.push({p:this.items.tcp,t:this.runAt});this.trail=this.trail.filter(v=>this.runAt-v.t<3000).slice(-120);}
 }
 drawRun(draw){
  const gl=this.gl,now=performance.now(),fresh=now-(this.runAt||0)<2500;let items=this.items;if(!items)return;
  const lerp=(a,b,t)=>b.map((v,i)=>(a?.[i]??v)+(v-(a?.[i]??v))*t),fraction=matchMedia('(prefers-reduced-motion: reduce)').matches?1:Math.min(1,(now-this.runAt)/110),old=this.itemsFrom;
  if(old)items={...items,tcp:items.tcp?lerp(old.tcp,items.tcp,fraction):null,objects:items.objects.map(o=>({...o,position:lerp(old.objects.find(p=>p.id===o.id)?.position,o.position,fraction)}))};this.displayItems=items;
  if(!this.runMeshes)this.runMeshes={box:this.buffer(boxMesh()),outline:this.buffer(boxMesh(true),gl.LINES),sphere:this.buffer(sphereMesh()),ring:this.buffer(ringMesh(),gl.LINE_STRIP),trail:this.buffer(new Float32Array(0),gl.LINE_STRIP)};
  const mesh=this.runMeshes,table=this.manifest.surfaces.find(s=>s.name==='table'),s=this.manifest.calibration.meters_per_map_unit||1,z=table.height*s;
  const paint=(entry,position,size,tint,opacity=1,soft=false)=>draw({...entry,tint,opacity,soft},modelAt(position,size));
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
  // A thin, clean table edge and a soft grounding shadow; both are display geometry.
  paint(mesh.box,[table.centre[0]*s,table.centre[1]*s,z-.013*s],[table.width*s,table.depth*s,.024*s],[.32,.24,.16]);
  gl.depthMask(false);
  paint(mesh.sphere,[table.centre[0]*s,table.centre[1]*s,z-.045*s],[table.width*.6*s,table.depth*.62*s,.013*s],[.02,.025,.03],.25);
  for(const object of items.objects){const colour=/red/i.test(object.label)?[.96,.28,.24]:[.28,.57,1],size=.036*items.scale;
   paint(mesh.sphere,[object.position[0],object.position[1],z+.001*s],[size,size,.002*s],[.03,.03,.03],.3);
   gl.depthMask(true);paint(mesh.box,object.position,size,colour);paint(mesh.outline,object.position,size*1.01,[.96,.97,1],.55);gl.depthMask(false);
   if(object.selected)paint(mesh.ring,[object.position[0],object.position[1],z+.003*s],size*.86,[.84,.99,.32],.95);
  }
  if(this.runPose){const base=this.runPose.base;paint(mesh.sphere,[base[0][3],base[1][3],z+.001*s],[.065*items.scale,.065*items.scale,.002*s],[.02,.025,.03],.24);}
  if(fresh){for(const hand of items.hands)paint(mesh.sphere,hand.position,hand.radius,[1,.53,.38],.24,true);
   if(items.tcp&&Number.isFinite(this.runFrame.stop_distance_m)){
    const distance=Math.min(Number.isFinite(this.runFrame.separation_m)?this.runFrame.separation_m*items.scale:Infinity,...items.hands.map(h=>Math.hypot(...h.position.map((v,i)=>v-items.tcp[i]))-h.radius)),stop=this.runFrame.stop_distance_m*items.scale,warm=this.runFrame.stopped?1:Math.max(0,Math.min(1,Math.max(1-(this.runFrame.speed_scale??1),1-(distance-stop)/(stop*2)))),colour=[.55,.85,.72].map((v,i)=>v+([1,.33,.2][i]-v)*warm);
    paint(mesh.sphere,items.tcp,this.runFrame.stop_distance_m*items.scale,colour,.04,true);paint(mesh.sphere,items.tcp,this.runFrame.stop_distance_m*items.scale*.96,colour,.02,true);paint(mesh.ring,items.tcp,this.runFrame.stop_distance_m*items.scale,colour,.4);
   }
  }
  const trail=(this.trail||[]).filter(v=>now-v.t<3000);if(trail.length>1){const values=new Float32Array(trail.flatMap(v=>[...v.p,.75,.95,.4,0,0,1]));gl.bindBuffer(gl.ARRAY_BUFFER,mesh.trail.buffer);gl.bufferData(gl.ARRAY_BUFFER,values,gl.DYNAMIC_DRAW);mesh.trail.count=trail.length;draw({...mesh.trail,opacity:.28},I());}
  gl.depthMask(true);gl.disable(gl.BLEND);this.onRunFrame?.(items,{fresh,simulated:!!this.runPose,replay:!!this.runFrame?.replay});
 }
 async setMode(mode){if(mode==='photo'&&this.photoUnavailable){this.onAppearance?.('Scene photo unavailable · CAD retained');return false;}if(mode==='photo'&&!this.photoReady){try{const generation=this.generation,camera=structuredClone(this.photo.camera),target=[...this.photo.target];const loading=this.photo.load(this.assetBase),controller=this.photo.loadController,timeout=setTimeout(()=>controller.abort(),12000);try{await loading;}finally{clearTimeout(timeout);}if(generation!==this.generation)return;this.sourceData=this.photo.data.slice();this.sourceMetadata=structuredClone(this.photo.metadata);this.sourceTarget=[...this.photo.target];transformGaussians(this.photo,this.manifest);this.photo.camera=camera;this.photo.target=target;this.photoReady=true;}catch{this.onAppearance?.('Scene photo unavailable · CAD retained');return false;}}if(this.runMode){this.runPhoto=mode==='photo';this.photo.dirty=true;this.onAppearance?.((this.textureNote||'CAD tabletop')+(this.runPhoto?' · static captured scene':''));this.photo.canvas.style.visibility=this.runPhoto?'visible':'hidden';this.photo.canvas.style.opacity=this.runPhoto?'1':'0';this.photo.canvas.style.pointerEvents='none';this.mode='cad';this.cad.style.opacity='1';this.cad.style.pointerEvents='auto';return true;}this.mode=mode;this.cubes.drawHandles();this.photo.canvas.style.opacity=mode==='photo'?'1':'0';this.photo.canvas.style.pointerEvents=mode==='photo'?'auto':'none';this.cad.style.opacity=mode==='cad'?'1':'0';this.cad.style.pointerEvents=mode==='cad'?'auto':'none';return true;}
 canvasRay(x,y){const c=this.photo.camera;if(!c)return null;const fit=Math.min(this.cad.clientWidth/c.width,this.cad.clientHeight/c.height),u=(x-(this.cad.clientWidth-c.width*fit)/2)/fit,v=(y-(this.cad.clientHeight-c.height*fit)/2)/fit;return {origin:this.photo.centre(),direction:transpose(c.rotation).map(row=>dot(row,[(u-c.K[0][2])/c.K[0][0],(v-c.K[1][2])/c.K[1][1],1]))};}
 project(p){const c=this.photo.camera;if(!c)return null;const t=c.translation,r=c.rotation,cam=r.map((row,i)=>dot(row,p)+t[i]);if(cam[2]<=0)return null;const w=this.cad.clientWidth,h=this.cad.clientHeight,s=Math.min(w/c.width,h/c.height);return [(c.K[0][0]*cam[0]/cam[2]+c.K[0][2])*s+(w-c.width*s)/2,(c.K[1][1]*cam[1]/cam[2]+c.K[1][2])*s+(h-c.height*s)/2];}
 planePoint(x,y,height){const c=this.photo.camera;if(!c)return null;const s=Math.min(this.cad.clientWidth/c.width,this.cad.clientHeight/c.height),u=(x-(this.cad.clientWidth-c.width*s)/2)/s,v=(y-(this.cad.clientHeight-c.height*s)/2)/s,ray=transpose(c.rotation).map(row=>dot(row,[(u-c.K[0][2])/c.K[0][0],(v-c.K[1][2])/c.K[1][1],1])),centre=this.photo.centre();if(Math.abs(ray[2])<1e-7)return null;const k=(height-centre[2])/ray[2];return k>0?centre.map((x,i)=>x+k*ray[i]):null;}
 render(frame=null){
  if(frame)this.renderFrame(frame);
  const gl=this.gl,canvas=this.cad,c=this.photo.camera;if(!canvas.clientWidth||!canvas.clientHeight)return;const ratio=Math.min(devicePixelRatio||1,2),width=Math.max(1,Math.round(canvas.clientWidth*ratio)),height=Math.max(1,Math.round(canvas.clientHeight*ratio));if(canvas.width!==width)canvas.width=width;if(canvas.height!==height)canvas.height=height;gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(!c||!this.manifest||this.mode!=='cad')return;
  gl.useProgram(this.program);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LESS);gl.disable(gl.CULL_FACE);gl.uniformMatrix3fv(this.uniforms.cameraRotation,false,flatten(c.rotation));gl.uniform3fv(this.uniforms.translation,c.translation);gl.uniform2f(this.uniforms.viewport,canvas.width,canvas.height);const s=Math.min(canvas.width/c.width,canvas.height/c.height);gl.uniform4f(this.uniforms.intrinsics,c.K[0][0]*s,c.K[1][1]*s,c.K[0][2]*s+(canvas.width-c.width*s)/2,c.K[1][2]*s+(canvas.height-c.height*s)/2);
  const draw=(entry,model)=>{if(!entry)return;gl.bindBuffer(gl.ARRAY_BUFFER,entry.buffer);for(let i=0;i<3;i++){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,3,gl.FLOAT,false,entry.textured?44:36,i*12);}gl.uniform1i(this.uniforms.softEdges,entry.soft?1:0);gl.uniform3fv(this.uniforms.tint,entry.tint||[1,1,1]);gl.uniform1f(this.uniforms.opacity,entry.opacity??1);gl.uniform1i(this.uniforms.textured,entry.textured?1:0);if(entry.textured){gl.enableVertexAttribArray(3);gl.vertexAttribPointer(3,2,gl.FLOAT,false,44,36);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.tableTexture);gl.uniform1i(this.uniforms.appearance,0);}else gl.disableVertexAttribArray(3);gl.uniformMatrix4fv(this.uniforms.model,false,flatten(model));gl.uniform1f(this.uniforms.points,entry.mode===gl.POINTS?(entry.pointSize||1):0);gl.drawArrays(entry.mode,0,entry.count);};
  const m=this.manifest,t=m.frames.workspace_from_map,scale=m.calibration.meters_per_map_unit||1;
  if(m.layers.surfaces||this.runMode){if(this.tableQuad&&!m.needs_rebuild){draw(this.tableQuad,I());if(!this.runMode)for(const p of this.proxies.filter(p=>p.name!=='table'))draw(p.geometry,I());}else if(m.needs_rebuild||this.runMode||!this.surface){for(const p of this.proxies.filter(p=>!this.runMode||p.name==='table'))draw(p.geometry,I());}else draw(this.surface,t);}
  if(m.layers.observed&&!this.runMode)draw(this.observed,t);
  if(this.voxelBuffer&&!this.runMode)draw(this.voxelBuffer,I());
  if(m.layers.robot||this.runMode){const r=m.robot,live=this.runMode?this.runPose:null,joints=live?(this.runEase?.value(performance.now(),matchMedia('(prefers-reduced-motion: reduce)').matches)||live.joints):(this.jointEase?.value(performance.now(),matchMedia('(prefers-reduced-motion: reduce)').matches)||r.joint_positions),base=live?live.base:xyz(r.translation.map(x=>x*scale),r.rotation.map(x=>x*Math.PI/180),r.scale*scale),poses={[this.description.base_link]:base},pending=[...this.description.joints];
   while(pending.length){let progressed=false;for(const joint of [...pending]){if(!poses[joint.parent])continue;let local=xyz(joint.origin.xyz,joint.origin.rpy),motion=I();if(['revolute','continuous'].includes(joint.type))motion=axisRotation(joint.axis,joints[joint.name]||0);poses[joint.child]=mul(poses[joint.parent],mul(local,motion));pending.splice(pending.indexOf(joint),1);progressed=true;}if(!progressed)break;}
   for(const part of this.robot){const visual=xyz(part.origin.xyz,part.origin.rpy),size=I();part.scale.forEach((x,i)=>size[i][i]=x);if(poses[part.link])draw(part.geometry,mul(poses[part.link],mul(visual,size)));}}
  if(this.routeBuffer&&(!this.runMode||performance.now()-(this.runAt||0)<2500)){draw(this.routeBuffer,I());draw({...this.routeNodes,pointSize:6},I());}
  if(this.runMode)this.drawRun(draw);
  this.cubes.draw(c,canvas,performance.now());
  this.onFrame?.();
 }
 dispose(){for(const remove of this.listeners)remove();++this.generation;this.release();this.cubes.dispose();cancelAnimationFrame(this.animation);cancelAnimationFrame(this.photo.animation);this.photo.observer.disconnect();this.photo.clearScene();this.photo.dispose();this.gl.deleteProgram(this.program);}
}
