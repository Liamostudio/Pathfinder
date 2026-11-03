(function(root){
'use strict';
function validateReplay(data){
 if(!data||data.schema_version!==1||data.mode!=='simulation replay'||!Array.isArray(data.frames)||!data.frames.length||data.frames.length>18000||!Array.isArray(data.events)||data.events.length>5000)throw Error('Unsupported or empty simulation replay');
 let previous=-1;const finite=v=>typeof v==='number'&&Number.isFinite(v),point=p=>Array.isArray(p)&&p.length===3&&p.every(finite);
 for(const f of data.frames){if(!finite(f.t)||f.t<0||f.t<previous||!point(f.scene?.arm_position)||!Array.isArray(f.scene.objects)||!Array.isArray(f.scene.hands)||!finite(f.speed_scale)||f.speed_scale<0||f.speed_scale>1)throw Error('Replay has an invalid trajectory frame');previous=f.t;
  for(const o of f.scene.objects)if(typeof o.id!=='string'||typeof o.label!=='string'||!point(o.position))throw Error('Replay has an invalid object');
  for(const h of f.scene.hands)if(!point(h.position))throw Error('Replay has an invalid hand');
 }
 if(data.events.some(e=>!finite(e.t)||typeof e.source!=='string'||typeof e.input!=='string'||typeof e.output!=='string'))throw Error('Replay has an invalid timeline');
 if(!previous)throw Error('Replay needs a positive duration');return data;
}
root.ReplayData={validateReplay};if(typeof module!=='undefined')module.exports={validateReplay};
if(typeof document==='undefined')return;
const $=id=>document.getElementById(id),ns='http://www.w3.org/2000/svg';let data=null,t=0,playing=false,last=0,lastEvents='';
function fail(error){playing=false;data=null;$('error').hidden=false;$('error').textContent='Replay error: '+error.message;$('play').textContent='Play';$('play').disabled=true;$('scene').replaceChildren();$('timeline').replaceChildren();}
function node(tag,attrs,text){const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;return e;}
const project=p=>[340+p[0]*650+p[1]*260,380+p[0]*120-p[1]*350-p[2]*700];
function draw(){if(!data)return;const frames=data.frames;let lo=0,hi=frames.length-1;while(lo<hi){const m=Math.ceil((lo+hi)/2);if(frames[m].t<=t)lo=m;else hi=m-1;}const f=frames[lo],svg=$('scene');svg.replaceChildren();
 const table=[[-.4,-.35,0],[.5,-.35,0],[.5,.4,0],[-.4,.4,0]].map(project);svg.append(node('polygon',{points:table.map(p=>p.join(',')).join(' '),fill:'#52634e',stroke:'#9eb18c'}));
 const line=(a,b,color,width)=>{a=project(a);b=project(b);svg.append(node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:color,'stroke-width':width,'stroke-linecap':'round'}));};
 const tip=f.scene.arm_position,base=[0,-.12,.02],elbow=[tip[0]*.5,tip[1]*.5-.06,Math.max(.17,tip[2]+.06)];line(base,elbow,'#d5fd51',14);line(elbow,tip,'#d5fd51',11);
 for(const o of f.scene.objects){const p=project(o.held?tip:o.position);svg.append(node('rect',{x:p[0]-12,y:p[1]-22,width:24,height:24,rx:3,fill:o.label.includes('red')?'#d77f73':'#7eafce',stroke:'#e3e9dc'}),node('text',{x:p[0]+18,y:p[1]-6,fill:'#e3e9dc','font-size':12},o.label));}
 for(const h of f.scene.hands){const p=project(h.position);svg.append(node('circle',{cx:p[0],cy:p[1],r:18,fill:'#eeb38e',stroke:'#ffdabd'}));}
 svg.append(node('text',{x:24,y:32,fill:'#d5fd51','font-size':12},'SCHEMATIC / RECORDED MUJOCO STATE'));
 const gate=f.hand_gate||{},state=gate.state||(f.stopped?'latched':f.speed_scale===0?'stop':f.speed_scale<1?'slow':'clear');$('gate').textContent=state.toUpperCase();$('gate').style.color=['stop','latched','stale'].includes(state)?'#a52c2c':'#405f23';$('scale').value=f.speed_scale;$('metrics').textContent=`${Math.round(f.speed_scale*100)}% speed · ${f.scene.hands.length} synthetic hands · t ${t.toFixed(2)} s`;
 $('scrub').value=t;$('time').textContent=`${t.toFixed(1)} / ${frames.at(-1).t.toFixed(1)} s`;
 const events=data.events.filter(e=>e.t<=t),key=events.map(e=>e.id).join(',');if(key!==lastEvents){lastEvents=key;$('timeline').replaceChildren(...events.slice().reverse().map(e=>{const li=document.createElement('li'),small=document.createElement('small'),input=document.createElement('strong'),output=document.createElement('p'),metrics=document.createElement('small');small.textContent=`${e.t.toFixed(2)} s · ${e.source}${e.scripted?' · scripted':''}`;input.textContent=e.input;output.textContent=e.output;metrics.textContent=`${Number(e.latency_ms||0).toFixed(1)} ms · $${Number(e.cost_usd||0).toFixed(4)} · ${JSON.stringify(e.tokens||{})} tokens`;li.append(small,input,output,metrics);return li;}));}
}
function load(value){data=validateReplay(value);t=0;lastEvents='';$('error').hidden=true;$('play').disabled=false;$('title').textContent=data.title||'Recorded simulator run';$('limits').textContent=(data.limitations||[]).join(' ');$('scrub').max=data.frames.at(-1).t;playing=new URLSearchParams(location.search).has('autoplay');$('play').textContent=playing?'Pause':'Play';draw();}
$('play').onclick=()=>{if(!data)return;if(t>=data.frames.at(-1).t)t=0;playing=!playing;$('play').textContent=playing?'Pause':'Play';};$('scrub').oninput=e=>{playing=false;t=Number(e.target.value);$('play').textContent='Play';draw();};
$('file').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>20*1024**2)throw Error('Replay exceeds 20 MiB');load(JSON.parse(await f.text()));}catch(error){fail(error);}};
function tick(now){const dt=Math.min(.1,(now-last)/1000);last=now;if(playing&&data){t=Math.min(data.frames.at(-1).t,t+dt*Number($('speed').value));if(t>=data.frames.at(-1).t){playing=false;$('play').textContent='Play';}draw();}requestAnimationFrame(tick);}requestAnimationFrame(tick);
const source=new URLSearchParams(location.search).get('source');if(source){const url=new URL(source,location.href);if(url.origin!==location.origin)fail(Error('Replay source must use this host'));else fetch(url).then(r=>{if(!r.ok)throw Error('Replay unavailable');return r.json();}).then(load).catch(fail);}else try{load(root.PATHFINDER_DEMO);}catch(error){fail(error);}
})(typeof window==='undefined'?globalThis:window);
