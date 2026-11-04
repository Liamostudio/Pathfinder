import {footprintFromDrag,gridShape,cellCorners,volumeCorners,heightFromDrag,EDGE_PAIRS,fidelityCellSize} from './workspace-voxels.js';
const $=id=>document.getElementById(id),ns='http://www.w3.org/2000/svg';
async function api(url,payload){const r=await fetch(url,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const v=await r.json();if(!r.ok)throw Error(v.error||'Working-area request failed');return v;}
const el=(name,attributes={})=>{const e=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attributes))e.setAttribute(k,v);return e;};
export async function resetNoGoCubes(root, name, isCurrent){
 const snapshot=await api(root+'/voxels');
 if(!isCurrent())return;
 const indices=snapshot.manual_blocked;
 if(!indices.length)return {snapshot,message:'No manual no-go cubes to reset.'};
 const cubes=`${indices.length} manual no-go ${indices.length===1?'cube':'cubes'}`;
 if(!window.confirm(`Reset ${cubes} in ${name}? Live observations stay in place.`))return;
 const result=await api(root+'/voxel-edits',{revision:snapshot.revision,policy_revision:snapshot.policy_revision,indices,blocked:false});
 return {snapshot:result,message:`Reset ${cubes}.`};
}
export class WorkAreaEditor{
 constructor({context,patch,notice,mode}){
  this.context=context;this.patch=patch;this.notice=notice;this.setMode=mode;this.token=0;this.mode=null;this.preview=null;this.stream=null;this.demo=null;this.liveTimer=null;this.pollBusy=false;
  const action=fn=>()=>Promise.resolve().then(fn).catch(e=>notice(e.message));
  $('wa-draw').onclick=action(()=>{if(!Number.isFinite(Number($('wa-cell').value))||Number($('wa-cell').value)<=0||!Number.isFinite(Number($('wa-height').value))||Number($('wa-height').value)<=0)throw Error('Enter a positive height and cube size first');this.setMode();$('ws-cad-canvas').scrollIntoView({block:'center'});this.mode='draw';this.renderer?.cubes.setEnabled(false);$('wa-cancel').hidden=false;$('ws-area-handles').classList.add('drawing');notice('Drag a rectangle across the tabletop, then pull the top handle up.');});
  $('wa-cancel').onclick=()=>{this.mode=null;this.preview=null;$('wa-cancel').hidden=true;$('ws-area-handles').classList.remove('drawing');this.paint();};
  $('wa-enabled').onchange=action(()=>{if(!this.manifest?.working_area)throw Error('Draw the tabletop footprint first');return this.commit({...this.manifest.working_area,enabled:$('wa-enabled').checked});});
  for(const [id,key] of [['wa-height','height'],['wa-cell','cell_size']])$(id).onchange=action(()=>{if(!this.manifest?.working_area)return;return this.commit({...this.manifest.working_area,[key]:Number($(id).value)*this.units});});
  $('wa-voxel-mode').onchange=()=>this.renderer?.cubes.setMode($('wa-voxel-mode').value);
  $('wa-pick-back').onclick=()=>this.renderer?.cubes.back();
  $('wa-row-block').onclick=()=>this.renderer?.cubes.edit(this.selectionIndices||this.renderer.cubes.row,true);$('wa-row-unblock').onclick=()=>this.renderer?.cubes.edit(this.selectionIndices||this.renderer.cubes.row,false);
  $('wa-clear-no-go').onclick=action(()=>this.resetNoGo());
  $('wa-fidelity').onchange=action(async()=>{const value=$('wa-fidelity').value;if(value==='custom'){$('wa-cell').focus();return;}const area=this.manifest?.working_area;if(!area)return;const size=fidelityCellSize(area,Number(value),this.units);await this.commit({...area,cell_size:size});});
  $('wa-demo').onclick=action(()=>this.toggleDemo());$('wa-reset').onclick=action(async()=>{await this.stopSources();await api(this.root+'/voxel-reset',{});await this.poll();});
  $('wa-anchor').onclick=action(async()=>{await this.stopSources();const source=$('wa-source-intrinsics').value.trim();notice('Detecting the marker in original image pixels…');const value=await api(this.root+'/marker-alignment',{view_id:$('wa-source').value,marker_id:Number($('wa-marker').value),marker_size_m:Number($('wa-marker-size').value),marker_size_measured:$('wa-measured').checked,intrinsics:source?JSON.parse(source):null});await this.patch({});notice(`Marker anchored · ${value.draft.marker_alignment.reprojection_px.toFixed(2)} px reprojection error. Robot alignment remains unverified.`);});
  $('wa-camera').onclick=action(async()=>{await this.stopSources();this.stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480}},audio:false});const v=$('wa-video');v.srcObject=this.stream;v.hidden=false;await v.play();$('wa-live-status').textContent='Camera preview only. Enter intrinsics, then register the visible ArUco marker.';});
  $('wa-video-file').onchange=action(async()=>{await this.stopSources();const f=$('wa-video-file').files[0];if(!f)return;this.videoURL=URL.createObjectURL(f);const v=$('wa-video');v.srcObject=null;v.src=this.videoURL;v.hidden=false;v.loop=false;await v.play();$('wa-live-status').textContent='Recorded RGB preview. Enter intrinsics for this frame resolution and register the marker.';});
  $('wa-replay').onclick=action(async()=>{if(!this.videoURL)throw Error('Open a recorded video first');$('wa-video').currentTime=0;await $('wa-video').play();});
  $('wa-live-register').onclick=action(()=>this.registerCamera());$('wa-live-stop').onclick=action(()=>this.stopSources());
  const svg=$('ws-area-handles');svg.onpointerdown=e=>this.pointerDown(e);svg.onpointermove=e=>this.pointerMove(e);svg.onpointerup=e=>this.pointerUp(e);svg.onpointercancel=()=>{this.drag=null;this.preview=null;this.mode=null;svg.classList.remove('drawing');this.paint();};
  this.timer=setInterval(()=>this.poll(),200);window.addEventListener('pagehide',()=>this.dispose());
 }
 setContext(key,manifest,renderer){
  const identity=key+'/'+manifest.run_id;if(this.identity&&this.identity!==identity){this.stopSources();this.preview=null;this.snapshot=null;this.mode=null;$('wa-voxel-edit-status').textContent='';}
  this.identity=identity;this.key=key;
  renderer.onVoxelEdit=(indices,blocked)=>this.editVoxels(indices,blocked);
  renderer.onVoxelMode=mode=>$('wa-voxel-mode').value=mode;
  renderer.onVoxelSelection=selection=>{this.selectionIndices=selection.selectedIndices;const single=selection.stage==='cells'&&selection.selectedIndices.length===1;$('wa-row-block').textContent=single?'Block selected voxel':'Block selected slice';$('wa-row-unblock').textContent=single?'Unblock selected voxel':'Unblock selected slice';const axis=selection.axis===null?'':'XYZ'[selection.axis];$('wa-pick-status').textContent=['slice','cells'].includes(selection.stage)?`${axis} slice ${selection.layer+1} · ${selection.stage==='cells'?'layer frozen · click a cell, double-click to toggle; Back to move layers':'move along the axis · click to freeze'}`:selection.stage==='axis'?`Voxel ${selection.voxel} vertex selected · click an X, Y or Z edge`:selection.voxel===null?'Hover a cube and click one of its vertices. Double-click toggles one voxel.':`Voxel ${selection.voxel} · click a vertex to choose an axis, or double-click to toggle`;for(const id of ['wa-row-block','wa-row-unblock'])$(id).disabled=!['slice','cells'].includes(selection.stage);};
  renderer.onVoxelError=message=>this.notice(message);
  if(this.boundRenderer!==renderer){this.boundRenderer=renderer;renderer.cubes.setMode($('wa-voxel-mode').value);}
  this.manifest=manifest;this.renderer=renderer;this.root='/api/workspaces/'+key;
  this.units=manifest.marker_alignment?.canonical_units_per_metre||(manifest.calibration.meters_per_map_unit?1/manifest.calibration.meters_per_map_unit:1);
  const unit=manifest.marker_alignment?(manifest.marker_alignment.metric_inputs_complete?'marker metres':'estimated marker metres'):manifest.calibration.meters_per_map_unit?'metres':'relative units';for(const n of document.querySelectorAll('.wa-unit'))n.textContent=unit;
  const area=manifest.working_area;$('wa-enabled').checked=!!area?.enabled;if(area){$('wa-height').value=+(area.height/this.units).toFixed(3);$('wa-cell').value=+(area.cell_size/this.units).toFixed(3);const count=gridShape(area).reduce((a,b)=>a*b,1);$('wa-fidelity').value=count<=256?'256':count<=1024?'1024':'4096';}
  const previous=$('wa-source').value;$('wa-source').replaceChildren(...manifest.sources.map(s=>new Option(s.source+' / '+s.name,s.name)));if([...$('wa-source').options].some(o=>o.value===previous))$('wa-source').value=previous;
  const anchor=manifest.marker_alignment;$('wa-alignment').textContent=anchor?`ArUco ${anchor.marker_id} · ${anchor.marker_size_m*1000} mm · ${anchor.intrinsics_source} · ${anchor.reprojection_px.toFixed(2)} px · robot alignment unverified`:'Unanchored visual area. Detect a marker to attach RGB observations.';
  this.paint();this.poll();
 }
 async editVoxels(indices,blocked){
  const root=this.root,identity=this.identity;let snap=await api(root+'/voxels');const target=blocked===null?!snap.manual_blocked.includes(indices[0]):blocked;
  const result=await api(root+'/voxel-edits',{revision:snap.revision,policy_revision:snap.policy_revision,indices,blocked:target});if(root!==this.root||identity!==this.identity)return;this.snapshot=result;this.paint();this.notice(`${indices.length===1?'Voxel '+indices[0]:indices.length+' voxels'} ${target?'manually blocked':'manually unblocked'}. Live occupancy is unchanged.`);
 }
 async resetNoGo(){
  if(this.resettingNoGo||!this.snapshot?.manual_blocked?.length)return;
  const root=this.root,identity=this.identity;
  const isCurrent=()=>this.root===root&&this.identity===identity;
  const status=$('wa-voxel-edit-status');
  this.resettingNoGo=true;
  $('wa-clear-no-go').disabled=true;
  status.textContent='';
  try{
   const result=await resetNoGoCubes(root,this.manifest.name||'this workspace',isCurrent);
   if(result&&isCurrent()){
    this.snapshot=result.snapshot;
    status.textContent=result.message;
   }
  }catch(e){if(isCurrent())status.textContent=e.message;}
  finally{this.resettingNoGo=false;this.paint();}
 }
 async commit(area){
  if(gridShape(area).reduce((a,b)=>a*b,1)>4096)throw Error('More than 4,096 cubes. Increase cube size or reduce the volume.');
  await this.stopSources();this.preview=null;this.snapshot=null;await this.patch({working_area:area});await this.poll();
 }
 paint(){
  if(!this.renderer||!this.manifest)return;const area=this.preview||this.manifest.working_area;this.renderer.cubes.reveal=this.drag?.kind==='height'?1:0;this.renderer.cubes.setEnabled(!this.drag&&this.mode!=='draw');this.renderer.setWorkingArea(area,this.manifest.marker_alignment,this.preview?null:this.snapshot);
  $('wa-clear-no-go').disabled=this.resettingNoGo||!!this.preview||!area?.enabled||!this.snapshot?.manual_blocked?.length;
  $('wa-dimensions').textContent=area?`${(area.width/this.units).toFixed(2)} × ${(area.depth/this.units).toFixed(2)} × ${(area.height/this.units).toFixed(2)} · ${gridShape(area).join(' × ')} cubes`:'';this.draw();
 }
 async poll(){
  if(!this.root||this.pollBusy)return;this.pollBusy=true;const root=this.root,identity=this.identity;
  try{const snap=await api(root+'/voxels');if(root!==this.root||identity!==this.identity)return;this.snapshot=snap;this.paint();const c=snap.counts;this.statusText=snap.enabled?`${snap.simulation?'SIMULATED PEOPLE/HANDS · ':snap.has_recorded_evidence?'RECORDED RGB PEOPLE/HANDS · ':''}${c.free} clear · ${c.occupied} unsafe · ${c.unknown} unknown${snap.fresh?'':' · no fresh observations'}`:'Draw a working area to start.';$('wa-status').textContent=this.statusText;$('wa-canvas-status').textContent=snap.enabled?this.statusText:'';}catch(e){$('wa-status').textContent='Occupancy unavailable · cells unsafe. '+e.message;$('wa-canvas-status').textContent='Observations unavailable · all cells unknown';this.snapshot=null;this.paint();}finally{this.pollBusy=false;}
 }
 position(e){const r=$('ws-area-handles').getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
 world(e){const m=this.manifest,s=m.calibration.meters_per_map_unit||1,xy=this.position(e),p=this.renderer.planePoint(...xy,m.surfaces[0].height*s);return p?.map(x=>x/s);}
 pointerDown(e){
  if(!this.renderer||this.renderer.mode!=='cad')return;
  if(e.target.dataset.height){e.stopPropagation();this.drag={kind:'height',start:this.position(e),area:structuredClone(this.manifest.working_area)};}
  else if(this.mode==='draw'){const p=this.world(e);if(!p)return;this.drag={kind:'footprint',start:p};}
  else return;
  $('ws-area-handles').setPointerCapture(e.pointerId);e.preventDefault();
 }
 pointerMove(e){
  if(!this.drag)return;const d=this.drag;
  if(d.kind==='footprint'){const p=this.world(e);if(p)this.preview=footprintFromDrag(d.start,p,this.manifest.surfaces[0],Number($('wa-cell').value)*this.units,Number($('wa-height').value)*this.units);}
  else{const a=d.area,s=this.manifest.calibration.meters_per_map_unit||1,p=[...a.centre,a.base+a.height],base=this.renderer.project(p.map(v=>v*s)),top=this.renderer.project([p[0]*s,p[1]*s,(p[2]+1)*s]),xy=this.position(e);if(base&&top)this.preview={...a,height:heightFromDrag(a.height,xy.map((v,i)=>v-d.start[i]),top.map((v,i)=>v-base[i]),a.cell_size)};}
  if(this.preview&&gridShape(this.preview).reduce((a,b)=>a*b,1)>4096){this.preview=null;this.notice('Volume too large: increase cube size or reduce its dimensions.');}this.paint();
 }
 async pointerUp(){
  if(!this.drag)return;this.drag=null;this.mode=null;$('wa-cancel').hidden=true;$('ws-area-handles').classList.remove('drawing');const a=this.preview;
  if(a)try{await this.commit(a);this.notice('Working area saved to the draft. Save a new workspace version to activate it.');}catch(e){this.preview=null;this.paint();this.notice(e.message);}
 }
 draw(){
  const svg=$('ws-area-handles');if(!this.renderer||this.drag)return;const signature=JSON.stringify([this.renderer.mode,this.renderer.photo.camera,this.preview||this.manifest?.working_area,this.manifest?.calibration.meters_per_map_unit,this.renderer.cad.clientWidth,this.renderer.cad.clientHeight]);if(signature===this.drawSignature)return;this.drawSignature=signature;svg.replaceChildren();if(this.renderer.mode!=='cad')return;svg.setAttribute('viewBox',`0 0 ${this.renderer.cad.clientWidth} ${this.renderer.cad.clientHeight}`);
  const area=this.preview||this.manifest?.working_area;if(!area?.enabled)return;const scale=this.manifest.calibration.meters_per_map_unit||1,points=volumeCorners(area).map(p=>this.renderer.project(p.map(v=>v*scale)));if(points.some(p=>!p))return;
  const polygon=el('polygon',{points:points.slice(4).map(p=>p.join(',')).join(' ')});svg.append(polygon);
  const base=this.renderer.project([area.centre[0]*scale,area.centre[1]*scale,area.base*scale]),top=this.renderer.project([area.centre[0]*scale,area.centre[1]*scale,(area.base+area.height)*scale]);
  if(base&&top){svg.append(el('line',{x1:base[0],y1:base[1],x2:top[0],y2:top[1]}));const handle=el('circle',{cx:top[0],cy:top[1],r:9,class:'wa-height-handle','data-height':'true','aria-label':'Drag working-area height'});svg.append(handle);const text=el('text',{x:top[0]+15,y:top[1]-8});text.textContent=(area.height/this.units).toFixed(2)+' ↑';svg.append(text);}
 }
 async toggleDemo(){
  if(this.demo){await this.stopSources();return;}
  if(!this.manifest?.working_area?.enabled)throw Error('Draw and enable a working area first');await this.stopSources();await api(this.root+'/voxel-reset',{});
  const snap=await api(this.root+'/voxels'),area=this.manifest.working_area,shape=gridShape(area),count=shape.reduce((a,b)=>a*b,1),root=this.root,source='simulated-person-hand-'+Date.now();let sequence=0,inflight=false;
  const tick=async()=>{if(inflight)return;inflight=true;try{const x=sequence%shape[0],index=(Math.min(shape[2]-1,1)*shape[1]+Math.floor(shape[1]/2))*shape[0]+x,corners=cellCorners(area,index),p=corners[0].map((v,i)=>(v+corners[6][i])/2);await api(root+'/voxel-observations',{revision:snap.revision,sequence:sequence++,source,mode:'simulation',points:[p],radius:area.cell_size*.4,free:Array.from({length:count},(_,i)=>i)});await this.poll();}catch(e){this.notice(e.message);}finally{inflight=false;}};
  this.demo=setInterval(tick,250);$('wa-demo').textContent='Stop simulated intruder';await tick();
 }
 frame(){const capturedAt=Date.now(),v=$('wa-video');if(v.readyState<2)throw Error('Start a video preview first');const c=document.createElement('canvas'),ratio=Math.min(1,640/v.videoWidth);c.width=Math.round(v.videoWidth*ratio);c.height=Math.round(v.videoHeight*ratio);c.getContext('2d').drawImage(v,0,0,c.width,c.height);return {jpeg:c.toDataURL('image/jpeg',.65).split(',')[1],canvas:c,captured_at_ms:capturedAt};}
 async registerCamera(){
  if(!this.manifest?.marker_alignment)throw Error('Anchor the workspace marker first');if(!this.manifest.working_area?.enabled)throw Error('Draw the working area first');
  if(this.demo){clearInterval(this.demo);this.demo=null;$('wa-demo').textContent='Simulate an intruder';}
  if(this.liveTimer)clearInterval(this.liveTimer);await api(this.root+'/voxel-reset',{});const intrinsics=JSON.parse($('wa-live-intrinsics').value||'null'),frame=this.frame();
  $('wa-live-status').textContent=`Registering visible marker at ${frame.canvas.width} × ${frame.canvas.height}…`;
  const camera=await api(this.root+'/voxel-camera',{jpeg:frame.jpeg,intrinsics,recorded:!!this.videoURL}),snap=await api(this.root+'/voxels'),root=this.root,source='people-hands-'+camera.camera_id;let sequence=0,busy=false,lastTime=-1;
  const tick=async()=>{const video=$('wa-video');if(busy||video.paused||video.ended||video.currentTime===lastTime)return;lastTime=video.currentTime;busy=true;try{const f=this.frame(),result=await api(root+'/voxel-detect',{...camera,revision:snap.revision,sequence:sequence++,source,jpeg:f.jpeg,captured_at_ms:f.captured_at_ms});if(root!==this.root)return;const out=$('wa-motion-preview');out.hidden=false;out.width=f.canvas.width;out.height=f.canvas.height;const ctx=out.getContext('2d');ctx.drawImage(f.canvas,0,0);ctx.strokeStyle='#d5fd51';ctx.fillStyle='#d5fd51';ctx.font='15px sans-serif';for(const d of result.detections){const [x,y,x1,y1]=d.box;ctx.strokeRect(x*out.width,y*out.height,(x1-x)*out.width,(y1-y)*out.height);ctx.fillText(d.kind,x*out.width,Math.max(16,y*out.height));}$('wa-live-status').textContent=`Local CPU · hands + people only · ${result.detections.length} detections · ${result.inference_ms} ms inference · RGB depth range is conservative`;await this.poll();}catch(e){$('wa-live-status').textContent=e.message+' · no new clear-space evidence';}finally{busy=false;}};
  this.liveTimer=setInterval(tick,200);await tick();
 }
 async stopSources(){
  if(this.demo){clearInterval(this.demo);this.demo=null;if(this.root)await api(this.root+'/voxel-reset',{}).catch(()=>{});}$('wa-demo').textContent='Simulate an intruder';
  if(this.liveTimer)clearInterval(this.liveTimer);this.liveTimer=null;for(const t of this.stream?.getTracks()||[])t.stop();this.stream=null;const video=$('wa-video');video.pause();video.srcObject=null;video.removeAttribute('src');video.hidden=true;if(this.videoURL)URL.revokeObjectURL(this.videoURL);this.videoURL=null;$('wa-motion-preview').hidden=true;$('wa-live-status').textContent='No live observation source. Cells return to unknown when stale.';
 }
 dispose(){clearInterval(this.timer);this.stopSources();}
}
