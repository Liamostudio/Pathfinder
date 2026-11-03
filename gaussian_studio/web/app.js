import {GaussianViewer} from './viewer.js';
import {QUALITY_PRESETS,estimateBuild,duration,remainingTime} from './quality.js';

const $=id=>document.getElementById(id);
const embedded=!$('library');
let viewer=null,selected=null,loaded=null,captures=[],providers={},files=[],busy=false,polling=false,uploadController=null;
let referenceSignature='',initialProviderSelection=true;
const imagePattern=/\.(jpe?g|png|webp|tiff?|bmp)$/i;
const mediaPattern=/\.(jpe?g|png|webp|tiff?|bmp|mp4|mov|mkv|avi|webm|m4v|json)$/i;
const videoPattern=/\.(mp4|mov|mkv|avi|webm|m4v)$/i,videoDurations=new WeakMap();
const controls=['orbitMode','panMode','autoOrbit','zoomOut','zoomIn','resetView','snapshot','exportSplat','exportPly','exportMetadata','camera'];

function toast(message){if(!$('toast'))return;$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,9000);}
function showError(error){toast(error.message||String(error));if($('status'))$('status').textContent=error.message||String(error);else $('viewerStatus').textContent=error.message||String(error);}
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let value;try{value=await response.json();}catch{throw Error(`Local service returned HTTP ${response.status}.`);}if(!response.ok)throw Error(value.error||`Request failed: ${response.status}`);return value;}
function download(url,name){const link=document.createElement('a');link.href=url;link.download=name;link.click();}
function uploadNames(sourceFiles){const used=new Set();return sourceFiles.map(file=>{let name=file.name,n=1;const dot=name.lastIndexOf('.');while(used.has(name))name=`${file.name.slice(0,dot)}-${++n}${file.name.slice(dot)}`;used.add(name);return name;});}
function enableControls(enabled){if(embedded)return;for(const id of controls)$(id).disabled=!enabled;}
try{viewer=new GaussianViewer($('viewer'),{onStatus:message=>$('viewerStatus').textContent=message});}catch(error){showError(error);}

async function loadScene(id){
 if(!viewer||loaded===id)return;
 loaded=id;enableControls(false);$('viewerStatus').textContent='Loading Gaussian splat…';
 try{
  const scene=await viewer.load(`/scenes/${id}`);if(!scene||loaded!==id)return;
  $('camera').replaceChildren(...scene.cameras.map((camera,index)=>new Option(`${camera.kind==='inspection'?'Inspection':'View '+String(index+1).padStart(2,'0')} · ${camera.name}`,index)));
  $('camera').disabled=false;
  if(embedded)return;
  $('empty').hidden=true;enableControls(true);
  $('sceneBadge').textContent=scene.generated?'PROVIDER OUTPUT':'FITTED MULTI-VIEW SCENE';
  $('views').replaceChildren(...(scene.generated?[]:scene.cameras.slice(0,24)).map((camera,index)=>{
   const button=document.createElement('button'),image=document.createElement('img'),label=document.createElement('span');
   image.src=`/scenes/${id}/previews/${String(index).padStart(3,'0')}.jpg`;image.alt=`Fitted render, camera ${index+1}`;image.loading='lazy';
   label.textContent=`VIEW ${String(index+1).padStart(2,'0')}`;button.append(image,label);button.onclick=()=>{viewer.capturedView(index);$('camera').value=index;};return button;
  }));
 }catch(error){if(loaded===id){loaded=null;enableControls(false);showError(error);}}
}
$('camera').addEventListener('change',()=>viewer?.capturedView(Number($('camera').value)));

if(embedded){
 const id=new URLSearchParams(location.search).get('scene');
 if(id&&/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(id))loadScene(id);
 else api('/api/captures').then(entries=>{const entry=entries.find(c=>c.ready);if(entry)loadScene(entry.id);else $('viewerStatus').textContent='Create a splat in Splat Studio first.';}).catch(showError);
}else{
 function updateEstimate(){
  const preset=QUALITY_PRESETS[$('steps').value];
  $('qualityDetail').textContent=`Up to ${preset.max_gaussians.toLocaleString()} Gaussians · ${preset.train_edge.toLocaleString()} px training`;
  if($('backend').value!=='local'){$('timeEstimate').textContent='Provider dependent';$('estimateHint').textContent='Queue and processing time are controlled by your provider.';return;}
  const entry=captures.find(c=>c.id===selected);
  let frames=entry?.capture?.images?.length||0,approximate=false;
  if(files.length){
   frames=files.filter(file=>imagePattern.test(file.name)).length;
   const videos=files.filter(file=>videoPattern.test(file.name)),budget=Number($('maxFrames').value),fps=Number($('sampleFps').value)||2;
   let sampled=0;
   for(const file of videos){const seconds=videoDurations.get(file);if(Number.isFinite(seconds))sampled+=Math.ceil(seconds*fps);else{sampled+=budget||160;approximate=true;}}
   frames+=budget?Math.min(sampled,budget):sampled;
  }
  if(!frames){$('timeEstimate').textContent='Add a capture for an estimate';$('estimateHint').textContent='Includes camera reconstruction, fitting and export.';return;}
  const estimate=estimateBuild(preset,frames,captures.map(c=>c.scene).filter(Boolean));
  $('timeEstimate').textContent=`About ${duration(estimate.low)} – ${duration(estimate.high)}`;
  $('estimateHint').textContent=`${approximate?'Rough video budget:':'For'} ${frames.toLocaleString()} frames · ${estimate.calibrated?'calibrated from local fits':'rough GPU estimate'}. Includes camera solve and export; upload and first-time CUDA compilation add time.`;
 }
 function probeVideo(file){
  if(!videoPattern.test(file.name)||videoDurations.has(file))return;
  videoDurations.set(file,null);const video=document.createElement('video'),url=URL.createObjectURL(file);
  let finished=false;const done=()=>{if(finished)return;finished=true;if(Number.isFinite(video.duration)&&video.duration>0)videoDurations.set(file,video.duration);video.removeAttribute('src');video.load();URL.revokeObjectURL(url);updateEstimate();};
  video.preload='metadata';video.onloadedmetadata=done;video.onerror=done;video.src=url;setTimeout(done,5000);
 }
 $('steps').onchange=updateEstimate;$('sampleFps').oninput=updateEstimate;$('maxFrames').onchange=updateEstimate;
 function syncAutoOrbit(){const on=Boolean(viewer?.autoOrbit);$('autoOrbit').classList.toggle('active',on);$('autoOrbit').setAttribute('aria-pressed',String(on));}
 $('orbitMode').onclick=()=>{if(!viewer)return;viewer.mode='orbit';$('orbitMode').classList.add('active');$('orbitMode').setAttribute('aria-pressed','true');$('panMode').classList.remove('active');$('panMode').setAttribute('aria-pressed','false');$('viewer').style.cursor='grab';$('interactionHint').textContent='DRAG TO ORBIT · SCROLL TO ZOOM · SHIFT + DRAG TO PAN';};
 $('panMode').onclick=()=>{if(!viewer)return;viewer.mode='pan';$('panMode').classList.add('active');$('panMode').setAttribute('aria-pressed','true');$('orbitMode').classList.remove('active');$('orbitMode').setAttribute('aria-pressed','false');$('viewer').style.cursor='move';$('interactionHint').textContent='DRAG TO PAN · SCROLL TO ZOOM · SELECT ORBIT TO ROTATE';};
 $('autoOrbit').onclick=()=>{if(viewer){viewer.autoOrbit=!viewer.autoOrbit;syncAutoOrbit();}};
 $('zoomIn').onclick=()=>viewer?.zoom(.8);$('zoomOut').onclick=()=>viewer?.zoom(1.25);
 $('resetView').onclick=()=>{viewer?.resetView();$('camera').value='0';};
 $('snapshot').onclick=()=>{if(!viewer?.data)return;viewer.render();$('viewer').toBlob(blob=>{if(!blob)return;const url=URL.createObjectURL(blob);download(url,`${selected||'splat'}-view.png`);setTimeout(()=>URL.revokeObjectURL(url),1000);},'image/png');};
 for(const [id,file] of [['exportSplat','scene.splat'],['exportPly','scene.ply'],['exportMetadata','scene.json']])$(id).onclick=()=>{if(loaded)download(`/scenes/${loaded}/${file}`,`${loaded}.${file.split('.').pop()}`);};

 function referenceImages(entry){
  const previous=$('referenceImage').value;
  const names=uploadNames(files);
  const options=files.length?files.map((file,index)=>({file,name:names[index]})).filter(({file})=>imagePattern.test(file.name)).map(({file,name})=>new Option(file.webkitRelativePath||name,name)):(entry?.capture?.images||[]).map(image=>new Option(image.source===image.name?image.name:`${image.source} · ${image.name}`,image.name));
  const signature=JSON.stringify(options.map(option=>[option.value,option.text]));if(signature===referenceSignature)return;referenceSignature=signature;
  $('referenceImage').replaceChildren(...(options.length?options:[new Option('First sampled frame','')]));
  if(options.some(option=>option.value===previous))$('referenceImage').value=previous;
 }
 function showFiles(){
  const total=files.reduce((sum,file)=>sum+file.size,0),images=files.filter(f=>imagePattern.test(f.name)).length,videos=files.filter(f=>/\.(mp4|mov|mkv|avi|webm|m4v)$/i.test(f.name)).length;
  $('fileSummary').textContent=files.length?`${images} images · ${videos} videos · ${(total/1024**2).toFixed(1)} MB`:'No files selected. Your originals stay intact.';
  $('fileList').replaceChildren(...files.slice(0,30).map((file,index)=>{const row=document.createElement('div'),name=document.createElement('span'),remove=document.createElement('button');row.className='file-row';name.textContent=file.name;remove.textContent='×';remove.setAttribute('aria-label',`Remove ${file.name}`);remove.onclick=()=>{if(busy)return;files.splice(index,1);showFiles();};row.append(name,remove);return row;}));
  if(files.length>30){const more=document.createElement('p');more.textContent=`+ ${files.length-30} more files`;more.className='help';$('fileList').append(more);}
  referenceImages(captures.find(c=>c.id===selected));$('remoteConsent').checked=false;updateEstimate();
 }
 function addFiles(incoming){if(busy)return;const seen=new Set(files.map(f=>`${f.webkitRelativePath||f.name}|${f.size}|${f.lastModified}`));let ignored=0;for(const file of incoming){if(!mediaPattern.test(file.name)){ignored++;continue;}const identity=`${file.webkitRelativePath||file.name}|${file.size}|${file.lastModified}`;if(!seen.has(identity)){files.push(file);seen.add(identity);probeVideo(file);}}if(ignored)toast(`Skipped ${ignored} unsupported files.`);showFiles();}
 $('files').onchange=()=>{addFiles($('files').files);$('files').value='';};
 $('folderButton').onclick=()=>$('folder').click();$('folder').onchange=()=>{addFiles($('folder').files);$('folder').value='';};
 $('clearFiles').onclick=()=>{if(!busy){files=[];showFiles();}};
 for(const event of ['dragenter','dragover'])$('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.add('dragging');});
 for(const event of ['dragleave','drop'])$('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.remove('dragging');});
 $('drop').addEventListener('drop',e=>addFiles(e.dataTransfer.files));

 function engineChanged(){
  const backend=$('backend').value,remote=backend!=='local',single=backend==='sam3d-fal';
  $('localSettings').hidden=remote;$('singleImageSettings').hidden=!single;$('consentWrap').hidden=!remote;$('remoteConsent').checked=false;
  $('modeLabel').textContent=remote?'External model API':'Local processing';
  $('engineHelp').textContent=backend==='local'?'Fits overlapping photos to a shared 3D scene. Requires an NVIDIA CUDA GPU and at least 2 distinct views.':single?'Predicts an object from one selected image or sampled video frame. Uses your fal account; other views are not fused.':'Sends all normalised images and sampled video frames to your configured HTTP provider. Requires the Splat Studio API adapter.';
  const host=providers['custom-http']?.endpoint?new URL(providers['custom-http'].endpoint).host:'your configured provider';
  $('consentText').textContent=single?'Send one selected image to fal.ai using my fal account’s billing.':`Send all normalised frames to ${host}, using my provider account.`;
  $('privacyLabel').textContent=remote?(single?'API MODE · ONE IMAGE SENT TO FAL.AI ON SUBMIT':'API MODE · NORMALISED FRAMES SENT ON SUBMIT'):'LOCAL ENGINE · YOUR CAPTURE STAYS HERE';
  referenceImages(captures.find(c=>c.id===selected));updateEstimate();
 }
 $('backend').onchange=engineChanged;
 async function providerStatus(){
  providers=await api('/api/providers');
  if(initialProviderSelection){if(providers['sam3d-fal']?.configured)$('backend').value='sam3d-fal';initialProviderSelection=false;}
  const provider=$('keyProvider').value,entry=providers[provider];
  $('keyStatus').textContent=entry?.configured?(entry.key_source==='server configuration'?'fal key loaded from server configuration (FAL_KEY). The key is never returned to this viewer.':'Session key configured · server memory only.'):'No key configured for this provider.';
  if(provider==='custom-http'){$('endpoint').value=entry?.endpoint||'';$('modelId').value=entry?.model||'';}
  engineChanged();
 }
 function providerChanged(){const custom=$('keyProvider').value==='custom-http';$('customFields').hidden=!custom;$('providerDescription').textContent=custom?'Connect your own Gaussian reconstruction service or an adapter for another model provider. All frames are sent to this endpoint.':'Predicts one object from one image. Uses your fal account’s billing. Meta Model API keys are not compatible.';$('apiKey').value='';providerStatus().catch(showError);}
 $('providerButton').onclick=()=>{if($('backend').value!=='local')$('keyProvider').value=$('backend').value;providerChanged();$('providerDialog').showModal();};
 $('keyProvider').onchange=providerChanged;
 $('providerDialog').addEventListener('close',()=>$('apiKey').value='');
 $('saveKey').onclick=async()=>{try{if(!$('apiKey').value.trim())throw Error('Enter a key, or use Clear key to remove the existing one.');await api('/api/providers/key',{provider:$('keyProvider').value,api_key:$('apiKey').value,endpoint:$('endpoint').value.trim(),model:$('modelId').value.trim()});$('apiKey').value='';await providerStatus();toast('Provider key configured for this server session.');}catch(error){$('keyStatus').textContent=error.message;}};
 $('clearKey').onclick=async()=>{try{const saved=providers[$('keyProvider').value]?.key_source==='server configuration';await api('/api/providers/key',{provider:$('keyProvider').value,api_key:''});$('apiKey').value='';await providerStatus();if(saved)toast('Session key cleared. The saved FAL_KEY will load again when the app restarts.');}catch(error){$('keyStatus').textContent=error.message;}};

 function update(entry){
  if(!entry)return;
  if(loaded&&loaded!==entry.id){loaded=null;viewer?.clearScene();enableControls(false);$('empty').hidden=false;$('camera').replaceChildren(new Option('Camera views',''));$('views').replaceChildren();$('viewerStatus').textContent='Waiting for this capture';syncAutoOrbit();}
  $('sceneName').textContent=entry.id.replace(/^capture-/,'Splat · ');
  if(!busy){$('status').textContent=entry.error||entry.message||(entry.ready?'Gaussian splat ready.':entry.stage.replaceAll('_',' '));$('detail').textContent=entry.running?(entry.step?`${entry.step.toLocaleString()} / ${entry.steps.toLocaleString()} steps · ${entry.elapsed_s||0} s · ${entry.gpu_memory_mb||0} MB GPU`:'Processing your capture. You can leave this tab open.'):entry.ready?(entry.generated?'External provider output at arbitrary scale.':'Fitted Gaussian scene at arbitrary scale. Ready to explore and export.'):'Choose source files and create a splat.';
   $('progress').value=entry.ready?1:entry.step&&entry.steps?entry.step/entry.steps:0;if(entry.running&&!entry.step)$('progress').removeAttribute('value');}
  $('statusBadge').textContent=entry.running?'BUILDING':entry.ready?'READY':entry.stage==='failed'?'FAILED':entry.stage==='cancelled'?'STOPPED':'IDLE';
  const remaining=remainingTime(entry);$('remaining').hidden=!entry.running;
  $('remaining').textContent=remaining?`Estimated remaining: ${duration(remaining.low)} – ${duration(remaining.high)} · updates as fitting progresses`:entry.stage.startsWith('remote_')?'Waiting for provider · exact remaining time is unavailable.':entry.stage==='exporting'?'Finalising exports and camera previews…':entry.stage==='training'?'Warming up · time remaining appears after 100 steps.':'Time remaining will update once Gaussian fitting starts.';
  $('frameCount').textContent=entry.capture?.images?.length.toLocaleString()||'—';
  $('registered').textContent=entry.generated?'N/A':entry.reconstruction?`${entry.reconstruction.registered} / ${entry.reconstruction.total}`:'—';
  $('gaussians').textContent=(entry.scene?.count||entry.gaussians)?.toLocaleString()||'—';
  $('reprojection').textContent=entry.generated?'N/A':entry.reconstruction?`${entry.reconstruction.mean_reprojection_error_px.toFixed(2)} px`:'—';
  $('cancel').hidden=!busy&&!entry.running;$('cancel').textContent=busy?'Stop upload':'Stop build';
  $('build').disabled=busy||entry.running;$('backend').disabled=busy;
  $('warnings').replaceChildren(...(entry.scene?.warnings||entry.reconstruction?.warnings||entry.capture?.warnings||[]).filter(text=>!text.includes('robot')&&!text.includes('arm is baked')).map(text=>{const p=document.createElement('p');p.textContent='↳ '+text;return p;}));
  const stage=entry.ready?'complete':entry.stage==='ingested'?'ingesting':['matching','reconstruction','undistorting','reconstructed'].includes(entry.stage)?'features':entry.stage.startsWith('remote_')||entry.stage==='exporting'?'training':entry.stage;
  for(const element of document.querySelectorAll('[data-stage]'))element.classList.toggle('active',element.dataset.stage===stage);
  referenceImages(entry);updateEstimate();if(entry.ready)loadScene(entry.id);
 }
 async function refresh(){
  if(polling)return;polling=true;
  try{captures=await api('/api/captures');if(!selected&&captures.length)selected=(captures.find(c=>c.running)||captures.find(c=>c.ready)||captures[0]).id;
   $('library').replaceChildren(...captures.map(entry=>{const button=document.createElement('button'),title=document.createElement('strong'),info=document.createElement('span');button.className='library-item'+(selected===entry.id?' active':'');button.disabled=busy;title.textContent=entry.id.replace(/^capture-/,'Splat · ');info.textContent=entry.ready?`${entry.scene.count.toLocaleString()} GAUSSIANS · ${entry.generated?'PROVIDER':'LOCAL'}`:entry.stage.replaceAll('_',' ').toUpperCase();button.append(title,info);button.onclick=()=>{selected=entry.id;$('remoteConsent').checked=false;update(entry);refresh();};return button;}));
   if(!captures.length){const empty=document.createElement('p');empty.className='help';empty.textContent='Your finished scenes will live here.';$('library').append(empty);}
   update(captures.find(c=>c.id===selected));
  }catch(error){showError(error);}finally{polling=false;}
 }
 $('refresh').onclick=refresh;
 $('build').onclick=async()=>{
  if(busy)return;
  const backend=$('backend').value,consent=$('remoteConsent').checked,image=$('referenceImage').value,sourceFiles=[...files];
  const options={...QUALITY_PRESETS[$('steps').value],sample_fps:Number($('sampleFps').value),max_frames:Number($('maxFrames').value),all_images:true,backend,image,prompt:$('objectPrompt').value,allow_external_upload:consent};
  try{
   if(!sourceFiles.length&&!selected)throw Error('Add images or a video first.');
   if(options.sample_fps<=0||options.sample_fps>30||!Number.isFinite(options.sample_fps))throw Error('Choose a video sampling rate between 0.1 and 30 fps.');
   if(sourceFiles.some(file=>file.size>8*1024**3))throw Error('Each file must be under 8 GiB. Split larger videos before uploading.');
   if(backend!=='local'){
    if(!providers[backend]?.configured)throw Error('Configure this provider’s API key in Provider settings first.');
    if(!consent)throw Error('Confirm sending your source material to the selected provider first.');
   }
   const usable=sourceFiles.filter(file=>imagePattern.test(file.name)||/\.(mp4|mov|mkv|avi|webm|m4v)$/i.test(file.name));
   if(sourceFiles.length&&!usable.length)throw Error('Choose at least one image or video; JSON timestamp files alone cannot be reconstructed.');
   if(backend==='local'&&sourceFiles.length&&usable.length<2&&imagePattern.test(usable[0]?.name))throw Error('Local reconstruction needs at least 2 distinct overlapping images. For one image, select a predictive API provider.');
   busy=true;$('build').disabled=true;$('backend').disabled=true;$('files').disabled=true;$('folderButton').disabled=true;$('clearFiles').disabled=true;$('cancel').hidden=false;$('cancel').textContent='Stop upload';$('statusBadge').textContent='UPLOADING';
   uploadController=new AbortController();let id=selected;
   if(sourceFiles.length){
    id=(await api('/api/captures',{})).id;selected=id;
    const names=uploadNames(sourceFiles);
    for(let i=0;i<sourceFiles.length;i++){
     if(uploadController.signal.aborted)throw new DOMException('Stopped','AbortError');
     $('status').textContent=`Uploading file ${i+1} of ${sourceFiles.length}`;$('detail').textContent=`${sourceFiles[i].name} · upload to this computer`;$('progress').value=i/sourceFiles.length;
     const response=await fetch(`/api/upload/${id}`,{method:'POST',headers:{'X-Filename':encodeURIComponent(names[i])},body:sourceFiles[i],signal:uploadController.signal});
     const result=await response.json();if(!response.ok)throw Error(result.error);
    }
    files=[];showFiles();
   }
   if(uploadController.signal.aborted)throw new DOMException('Stopped','AbortError');
   $('status').textContent='Starting Gaussian reconstruction…';$('statusBadge').textContent='QUEUED';
   const job=await api('/api/run',{id,...options});selected=job.id;$('remoteConsent').checked=false;
   if(uploadController.signal.aborted)await api('/api/cancel',{id:job.id});
  }catch(error){if(error.name==='AbortError'){toast('Upload stopped. Already uploaded files remain in your capture.');$('status').textContent='Upload stopped.';}else showError(error);}
  finally{busy=false;uploadController=null;$('backend').disabled=false;$('files').disabled=false;$('folderButton').disabled=false;$('clearFiles').disabled=false;$('build').disabled=false;$('cancel').hidden=true;await refresh();}
 };
 $('cancel').onclick=async()=>{if(busy){uploadController?.abort();return;}try{await api('/api/cancel',{id:selected});await refresh();}catch(error){showError(error);}};
 providerStatus().catch(showError);refresh();setInterval(refresh,2000);
}
