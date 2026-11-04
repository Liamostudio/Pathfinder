import {confirmation} from './confirmation.js';
'use strict';
const $=id=>document.getElementById(id);let state=null,pending=false,lastJob=null,popup=null,modelSignature='',actionQueue=Promise.resolve();
const confirmAction=confirmation($('confirmation'),$('confirmation-message'),$('confirmation-cancel'),$('confirmation-accept'),[document.querySelector('main'),document.querySelector('header')]);
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});const data=await response.json();if(!response.ok)throw Error(data.error||'Launcher request failed');return data;}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function text(tag,value){const node=document.createElement(tag);node.textContent=value;return node;}
function light(id,status){$(id).className='light '+status;}
function render(value){
 state=value;$('preview').hidden=!value.preview;
 for(const [id,key] of [['arm-unpowered','arm_unpowered'],['hands-out','hands_out']])$(id).checked=value.safety[key];
 const busy=value.job?.status==='running',safe=Object.values(value.safety).every(Boolean);
 for(const button of document.querySelectorAll('[data-requires-safety]'))button.disabled=busy||!safe||!!value.take;
 $('check').disabled=busy;$('relay-stop').disabled=busy||!!value.take;
 $('take-end').disabled=!value.take||pending;$('take-start').disabled=!safe||busy||!!value.take||value.mode!=='live';
 $('selfcheck').disabled=!safe||busy||!!value.take||(value.mode==='live'&&value.services.deck?.running);
 $('model').disabled=busy||!!value.take;for(const id of ['sam-people','second-camera'])$(id).disabled=busy||!!value.take;
 const signature=JSON.stringify(value.models);if(signature!==modelSignature){modelSignature=signature;$('model').replaceChildren(...value.models.map(name=>{const option=text('option',name==='fake'?'fake — free rehearsal':name==='spark'?'spark — standard, for filming':name);option.value=name;return option;}));}
 $('model').value=value.options.model;$('sam-people').checked=value.options.sam_people;$('second-camera').checked=value.options.second_camera;
 const checked=value.checks.length,failed=value.checks.some(c=>c.status==='fail'),warn=value.checks.some(c=>c.status==='warn');
 light('hardware-light',failed?'fail':warn?'warn':checked?'pass':'');light('setup-light',value.setup_ready?'pass':value.setup_choice==='setup'?'warn':'');light('live-light',value.mode==='live'?'pass':'');light('controls-light',value.take?'warn':value.mode==='live'?'pass':'');
 const checkSignature=JSON.stringify(value.checks);if($('checks').dataset.signature!==checkSignature){$('checks').dataset.signature=checkSignature;$('checks').replaceChildren(...value.checks.map(check=>{const row=document.createElement('li'),dot=document.createElement('span');dot.className='light '+check.status;dot.setAttribute('aria-label',check.status);const description=text('div',check.message);description.className='check-text';if(check.thumbnail?.startsWith('data:image/jpeg;base64,')){const image=document.createElement('img');image.src=check.thumbnail;image.alt=check.id+' camera thumbnail';description.append(image);}row.append(dot,description);if(check.service){const stop=text('button','Stop '+check.service);stop.dataset.service=check.service;stop.disabled=busy||!!value.take;stop.onclick=async()=>{const external=!state.services[check.service]||state.services[check.service].external;const confirmed=!external||await confirmAction('Stop existing '+check.service+' PID '+(state.details.ports?.[check.service]?.pid||'?')+'? Only that PID will be signalled; its process group is left alone.');if(confirmed)perform(()=>api('/api/stop-service',{service:check.service,confirmed:external}));};row.append(stop);}return row;}));}
 for(const stop of $('checks').querySelectorAll('[data-service]'))stop.disabled=busy||!!value.take;
 const workspace=value.live.workspace||value.details.workspace,active=workspace?.active;
 $('workspace-status').textContent=workspace?.jobs?.length?'Reconstruction: '+workspace.jobs.map(job=>job.state+' / '+job.stage).join(', '):value.setup_choice==='setup'&&!value.setup_ready?'Review, save and activate a new version in Deck; a ready reconstruction alone is not active.':active?`Active saved workspace · ${active.id} / ${active.version} · ${workspace.manual_count} manual no-go cubes`:'No Ready active workspace. Save and activate in Deck, or use the existing workspace.';
 $('no-go-clear-status').textContent=value.manual_reset||'Only manual restrictions are cleared after confirmation. Live detections and unknown cells stay unchanged.';
 $('clear-no-go').disabled=!safe||busy||!!value.take||!value.services.deck?.running||!active;
 $('steps').replaceChildren(...(value.job?.lines||[]).map(line=>text('li',line)));
 $('countdown').hidden=value.job?.countdown_s==null;$('countdown').textContent=`Hands out of both views · calibration ${value.job?.countdown_s??0}s`;
 if(value.error)notice(value.error,true);else if(value.job)notice(value.job.action+' · '+value.job.status+(value.take?' · take recording':''));else notice('Check the hardware, confirm both safety boxes, then choose Setup or the existing workspace.');
 $('take-counter').textContent=value.take?`TAKE ${String(value.take.number).padStart(3,'0')} · RECORDING`:value.last_take?`Take ${String(value.last_take.number).padStart(3,'0')} saved`:'No take recorded';
 $('take-download').hidden=!value.last_take;if(value.last_take){$('take-download').href=value.last_take.url;$('take-download').download=`take-${String(value.last_take.number).padStart(3,'0')}.json`;}
 $('open-deck').href=value.links.deck;$('open-workspace').href=value.links.workspace;$('open-setup').href=value.links.setup;
 const live=value.live,gate=live.gate,sam=live.sam,now=Date.now()/1000,stale=now-(live.updated||0)>3;
 const rows=[stale?'STATUS STALE · local safety continues independently':live.error||`Hand gate: ${gate?.state||'not connected'} · ${gate?.fps??0} fps`,
  ...Object.entries(gate?.cameras||{}).map(([name,c])=>`${name}: ${Math.round(c.age_s*1000)} ms old · ${c.hands} hands`),
  `SAM: ${sam?.loaded?'loaded':'not running / not ready'}`,
  `Voxel source names seen: hand gate ${live.voxels?.hand_gate_seen?'yes':'waiting'} · SAM people ${live.voxels?.sam_people_seen?'yes':value.options.sam_people?'waiting':'not requested'}` ,
  ...(live.voxels?.sources||[]).map(source=>`${source.name}: ${source.age_ms} ms old`),
  `Spark: last $${Number(live.spark?.last_usd||0).toFixed(5)} · total $${Number(live.spark?.total_usd||0).toFixed(5)} (${live.spark?.scope||'retained Deck timeline'})`];
 if(value.preview)rows.unshift('FAKE SERVICES PREVIEW · synthetic status, no paid calls');
 $('live-status').replaceChildren(...rows.map(row=>text('div',row)));
 const tls=value.details.tls,ip=value.details.network?.ip;$('relay-address').textContent=ip?`Wi-Fi ${ip} · https://${ip}:8772`:'No Wi-Fi IPv4 found; connect to the same Wi-Fi as the phone.';
 $('tls-status').textContent=tls?.exists?tls.match?'Certificate already exists for '+ip:'IP mismatch: certificate must be reviewed locally; private keys will not be overwritten.':'No certificate yet.';
 $('ca-path').textContent=tls?'Install on iPhone: '+tls.ca_path:'';$('token-path').textContent=tls?'Pairing token file (contents private): '+tls.token_path:'';
 const logSignature=JSON.stringify(value.services);if($('logs').dataset.signature!==logSignature){$('logs').dataset.signature=logSignature;$('logs').replaceChildren(...Object.entries(value.services).map(([name,service])=>{const item=document.createElement('details');item.append(text('summary',`${name} · ${service.running?'running':'stopped'} · PID ${service.pid}`),text('pre',service.log||'No output yet'));return item;}));}
 $('logs-path').textContent='Logs and take files: recordings/launcher/ (Read sanitised logs also shows the folder path).';
 if(value.job?.status==='completed'&&value.job.id!==lastJob){lastJob=value.job.id;if(popup&&value.open_url){popup.location=value.open_url;popup=null;}else if(popup){popup.close();popup=null;}}
 if(value.job?.status==='failed'||value.job?.status==='cancelled'){popup?.close();popup=null;}
}
function perform(action){const task=actionQueue.then(async()=>{pending=true;try{await action();render(await api('/api/state'));}catch(error){notice(error.message,true);popup?.close();popup=null;}finally{pending=false;}});actionQueue=task.catch(()=>{});return task;}
async function actionBody(action){const names=action==='stop_all'?Object.keys(state.services):['setup','live','selfcheck'].includes(action)?['relay','deck','sam']:action==='relay_stop'?['relay']:[];const external=names.filter(name=>state.services[name]?.external&&state.services[name].running);if(external.length&&!await confirmAction('Stop existing services: '+external.map(name=>name+' PID '+state.services[name].pid).join(', ')+'? Only those individual PIDs will be signalled, never their process groups.'))return null;return {action,confirm_external:external.length>0};}
for(const [id,action] of [['check','check'],['setup','setup'],['existing','existing'],['live','live'],['reset','reset'],['selfcheck','selfcheck'],['tls','tls'],['relay-start','relay_start'],['relay-stop','relay_stop']])$(id).onclick=async()=>{const body=await actionBody(action);if(!body)return;if(['setup','live'].includes(action))popup=window.open('about:blank','_blank');perform(()=>api('/api/action',body));};
$('stop-all').onclick=async()=>{const body=await actionBody('stop_all');if(body)api('/api/action',body).then(()=>poll()).catch(error=>notice(error.message,true));};
$('clear-no-go').onclick=async()=>{const active=state.live.workspace?.active||state.details.workspace?.active;if(!active||!await confirmAction('Clear ALL manual no-go cubes in '+active.id+' / '+active.version+' and start fresh? Live detections and unknown cells will stay unchanged.'))return;perform(()=>api('/api/clear-no-go',{confirmed:true,workspace_id:active.id,version_id:active.version}));};
for(const id of ['arm-unpowered','hands-out'])$(id).onchange=()=>{const value={arm_unpowered:$('arm-unpowered').checked,hands_out:$('hands-out').checked};perform(()=>api('/api/safety',value));};
for(const id of ['model','sam-people','second-camera'])$(id).onchange=()=>{const value={model:$('model').value,sam_people:$('sam-people').checked,second_camera:$('second-camera').checked};perform(()=>api('/api/options',value));};
$('open-logs').onclick=()=>perform(()=>api('/api/open-logs',{}));
$('take-start').onclick=()=>perform(()=>api('/api/take-start',{}));$('take-end').onclick=()=>perform(()=>api('/api/take-end',{}));
let pollBusy=false;async function poll(){if(pollBusy||pending)return;pollBusy=true;try{render(await api('/api/state'));}catch(error){notice('Launcher unavailable · '+error.message,true);}finally{pollBusy=false;}}
poll().then(()=>{if(!state?.job)perform(()=>api('/api/action',{action:'check'}));});setInterval(poll,800);
