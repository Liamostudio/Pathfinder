// Anisotropic Gaussian projection and back-to-front alpha compositing. No CDN/runtime dependencies.
const VERTEX=`#version 300 es
precision highp float;
layout(location=0) in vec2 corner;
layout(location=1) in vec3 position;
layout(location=2) in vec3 scale;
layout(location=3) in vec4 quat;
layout(location=4) in vec4 colour;
uniform mat3 cameraRotation;
uniform vec3 cameraTranslation;
uniform vec4 intrinsics;
uniform vec2 viewport;
out vec2 local;
out vec4 rgba;
void main(){
 vec3 p=cameraRotation*position+cameraTranslation;
 if(p.z<0.02){gl_Position=vec4(5.,5.,0.,1.);local=vec2(10.);rgba=vec4(0.);return;}
 vec4 q=normalize(quat);float w=q.x,x=q.y,y=q.z,z=q.w;
 mat3 rot=mat3(1.-2.*(y*y+z*z),2.*(x*y+w*z),2.*(x*z-w*y),
  2.*(x*y-w*z),1.-2.*(x*x+z*z),2.*(y*z+w*x),
  2.*(x*z+w*y),2.*(y*z-w*x),1.-2.*(x*x+y*y));
 mat3 M=cameraRotation*rot*mat3(scale.x,0.,0.,0.,scale.y,0.,0.,0.,scale.z);
 vec3 jx=vec3(intrinsics.x/p.z,0.,-intrinsics.x*p.x/(p.z*p.z));
 vec3 jy=vec3(0.,intrinsics.y/p.z,-intrinsics.y*p.y/(p.z*p.z));
 vec3 a=vec3(dot(jx,M[0]),dot(jx,M[1]),dot(jx,M[2]));
 vec3 b=vec3(dot(jy,M[0]),dot(jy,M[1]),dot(jy,M[2]));
 float xx=dot(a,a)+0.3,xy=dot(a,b),yy=dot(b,b)+0.3;
 float mid=(xx+yy)*0.5,delta=sqrt(max(0.00001,(xx-yy)*(xx-yy)*0.25+xy*xy));
 float l1=mid+delta,l2=max(0.1,mid-delta);
 vec2 axis=abs(xy)>0.00001?normalize(vec2(xy,l1-xx)):(xx>yy?vec2(1.,0.):vec2(0.,1.));
 vec2 major=axis*min(sqrt(l1),500.);vec2 minor=vec2(-axis.y,axis.x)*min(sqrt(l2),500.);
 vec2 centre=intrinsics.xy*p.xy/p.z+intrinsics.zw;
 vec2 pixel=centre+3.*(corner.x*major+corner.y*minor);
 gl_Position=vec4(2.*pixel.x/viewport.x-1.,1.-2.*pixel.y/viewport.y,0.,1.);
 local=corner*3.;rgba=colour;
}`;
const FRAGMENT=`#version 300 es
precision highp float;
in vec2 local;in vec4 rgba;out vec4 outputColour;
void main(){float d=dot(local,local);if(d>9.)discard;float a=min(0.99,rgba.a*exp(-0.5*d));if(a<0.0039)discard;outputColour=vec4(rgba.rgb*a,a);}`;
const dot=(a,b)=>a.reduce((s,x,i)=>s+x*b[i],0),sub=(a,b)=>a.map((x,i)=>x-b[i]),add=(a,b)=>a.map((x,i)=>x+b[i]),mul=(a,s)=>a.map(x=>x*s),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],norm=a=>mul(a,1/(Math.hypot(...a)||1));
export class GaussianViewer {
 constructor(canvas,{onStatus=()=>{}}={}){
  this.canvas=canvas;this.onStatus=onStatus;this.gl=canvas.getContext('webgl2',{antialias:false,preserveDrawingBuffer:true});
  if(!this.gl)throw Error('WebGL 2 is required for Gaussian rendering.');
  const gl=this.gl;const compile=(kind,source)=>{const shader=gl.createShader(kind);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  this.program=gl.createProgram();gl.attachShader(this.program,compile(gl.VERTEX_SHADER,VERTEX));gl.attachShader(this.program,compile(gl.FRAGMENT_SHADER,FRAGMENT));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  gl.useProgram(this.program);this.uniforms=Object.fromEntries(['cameraRotation','cameraTranslation','intrinsics','viewport'].map(n=>[n,gl.getUniformLocation(this.program,n)]));
  gl.bindVertexArray(gl.createVertexArray());const corners=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,corners);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,1,1,-1,-1,1,1,-1,1]),gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);
  this.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);for(const [i,size,offset] of [[1,3,0],[2,3,12],[3,4,24],[4,4,40]]){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,size,gl.FLOAT,false,56,offset);gl.vertexAttribDivisor(i,1);}
  gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.disable(gl.DEPTH_TEST);
  this.dirty=true;this.data=null;this.camera=null;this.target=[0,0,0];this.up=[0,-1,0];this.drag=null;
  canvas.addEventListener('pointerdown',e=>{if(!this.camera)return;this.drag={x:e.clientX,y:e.clientY,button:e.button};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!this.drag)return;const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.x=e.clientX;this.drag.y=e.clientY;
   if(this.drag.button===2||e.shiftKey)this.pan(dx,dy);else this.orbit(dx,dy);});
  canvas.addEventListener('pointerup',()=>this.drag=null);canvas.addEventListener('pointercancel',()=>this.drag=null);canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('wheel',e=>{e.preventDefault();if(!this.camera)return;const c=this.centre();this.setLookAt(add(this.target,mul(sub(c,this.target),Math.exp(Math.max(-300,Math.min(300,e.deltaY))*.001))),this.target);},{passive:false});
  this.observer=new ResizeObserver(()=>{this.dirty=true;});this.observer.observe(canvas);
  const draw=()=>{if(this.dirty)this.render();this.animation=requestAnimationFrame(draw);};draw();
 }
 async load(base){
  const version=this.loadVersion=(this.loadVersion||0)+1;
  this.loadController?.abort();const controller=this.loadController=new AbortController();
  let metadata,raw;
  try{[metadata,raw]=await Promise.all([fetch(base+'/scene.json',{signal:controller.signal}).then(r=>{if(!r.ok)throw Error('Scene metadata unavailable');return r.json();}),fetch(base+'/scene.splat',{signal:controller.signal}).then(r=>{if(!r.ok)throw Error('Splat file unavailable');return r.arrayBuffer();})]);}
  catch(e){if(version!==this.loadVersion)return null;controller.abort();throw e;}
  if(version!==this.loadVersion)return null;
  if(raw.byteLength%32!==0||raw.byteLength===0)throw Error('Malformed .splat data');
  const n=raw.byteLength/32;if(n>500000)throw Error('Viewer budget exceeded: maximum 500,000 Gaussians');
  const input=new DataView(raw),values=new Float32Array(n*14);
  for(let i=0;i<n;i++){const offset=i*32,j=i*14;for(let k=0;k<6;k++){const x=input.getFloat32(offset+k*4,true);if(!Number.isFinite(x)||(k>=3&&x<=0))throw Error('Invalid Gaussian parameters');values[j+k]=x;}for(let k=0;k<4;k++)values[j+6+k]=(input.getUint8(offset+28+k)-128)/128;for(let k=0;k<4;k++)values[j+10+k]=input.getUint8(offset+24+k)/255;}
  this.data=values;this.count=n;this.metadata=metadata;this.indices=Array.from({length:n},(_,i)=>i);this.sorted=new Float32Array(values.length);
  this.target=metadata.bounds[0].map((x,i)=>(x+metadata.bounds[1][i])/2);
  this.up=norm(metadata.cameras.reduce((sum,c)=>add(sum,c.world_to_camera[1].slice(0,3).map(x=>-x)),[0,0,0]));
  this.capturedView(0);this.onStatus(`${n.toLocaleString()} ${metadata.generated?'SAM 3D':'trained'} Gaussians`);return metadata;
 }
 clearScene(){this.loadVersion=(this.loadVersion||0)+1;this.loadController?.abort();this.data=null;this.camera=null;this.metadata=null;this.dirty=true;}
 capturedView(index){if(!this.metadata)return;const c=this.metadata.cameras[index];if(!c)return;this.camera={rotation:c.world_to_camera.slice(0,3).map(row=>row.slice(0,3)),translation:c.world_to_camera.slice(0,3).map(row=>row[3]),K:c.K,width:c.width,height:c.height};this.dirty=true;}
 centre(){const {rotation:r,translation:t}=this.camera;return [0,1,2].map(i=>-r.reduce((sum,row,j)=>sum+row[i]*t[j],0));}
 setLookAt(centre,target){const forward=norm(sub(target,centre));let right=norm(cross(forward,this.up));if(Math.hypot(...right)<.01)right=[1,0,0];const down=norm(cross(forward,right));const r=[right,down,forward];const f=.5*this.canvas.width/Math.tan(Math.PI/6);this.camera={rotation:r,translation:r.map(row=>-dot(row,centre)),K:[[f,0,this.canvas.width/2],[0,f,this.canvas.height/2],[0,0,1]],width:this.canvas.width,height:this.canvas.height};this.dirty=true;}
 orbit(dx,dy){let offset=sub(this.centre(),this.target);const rotate=(v,axis,angle)=>add(add(mul(v,Math.cos(angle)),mul(cross(axis,v),Math.sin(angle))),mul(axis,dot(axis,v)*(1-Math.cos(angle))));offset=rotate(offset,this.up,-dx*.006);const right=norm(this.camera.rotation[0]);const proposal=rotate(offset,right,-dy*.006);if(Math.abs(dot(norm(proposal),this.up))<.98)offset=proposal;this.setLookAt(add(this.target,offset),this.target);}
 pan(dx,dy){const c=this.centre(),scale=Math.hypot(...sub(c,this.target))*.0018;const delta=add(mul(this.camera.rotation[0],-dx*scale),mul(this.camera.rotation[1],-dy*scale));this.target=add(this.target,delta);this.setLookAt(add(c,delta),this.target);}
 render(){
  this.dirty=false;const gl=this.gl,canvas=this.canvas;const ratio=Math.min(devicePixelRatio||1,2);const w=Math.max(1,Math.round(canvas.clientWidth*ratio)),h=Math.max(1,Math.round(canvas.clientHeight*ratio));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}gl.viewport(0,0,w,h);gl.clearColor(.05,.06,.07,1);gl.clear(gl.COLOR_BUFFER_BIT);if(!this.data||!this.camera)return;
  const {rotation:r,translation:t,K,width,height}=this.camera;
  this.indices.sort((a,b)=>{const i=a*14,j=b*14;return r[2][0]*(this.data[j]-this.data[i])+r[2][1]*(this.data[j+1]-this.data[i+1])+r[2][2]*(this.data[j+2]-this.data[i+2]);});
  for(let i=0;i<this.count;i++)this.sorted.set(this.data.subarray(this.indices[i]*14,this.indices[i]*14+14),i*14);
  gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,this.sorted,gl.DYNAMIC_DRAW);gl.useProgram(this.program);
  gl.uniformMatrix3fv(this.uniforms.cameraRotation,false,new Float32Array([r[0][0],r[1][0],r[2][0],r[0][1],r[1][1],r[2][1],r[0][2],r[1][2],r[2][2]]));gl.uniform3fv(this.uniforms.cameraTranslation,t);
  const s=Math.min(w/width,h/height);gl.uniform4f(this.uniforms.intrinsics,K[0][0]*s,K[1][1]*s,K[0][2]*s+(w-width*s)/2,K[1][2]*s+(h-height*s)/2);gl.uniform2f(this.uniforms.viewport,w,h);gl.drawArraysInstanced(gl.TRIANGLES,0,6,this.count);
 }
 dispose(){cancelAnimationFrame(this.animation);this.observer.disconnect();this.gl.deleteBuffer(this.buffer);this.gl.deleteProgram(this.program);}
}
