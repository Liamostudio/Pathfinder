export function gateView(feed,now=Date.now()/1000){
 if(!feed)return {state:'NOT CONNECTED',scale:0,alert:true,metrics:'No hand-gate endpoint connected.'};
 const age=(Number(feed.stale_s)||0)+Math.max(0,now-Number(feed.updated));
 if(!Number.isFinite(age)||!Number.isFinite(feed.scale)||feed.scale<0||feed.scale>1||!['clear','slow','stop','latched','stale'].includes(feed.state))return {state:'NOT CONNECTED',scale:0,alert:true,metrics:'Invalid hand-gate feed.'};
 const stale=age>.15||feed.state==='stale',state=stale?'STALE':feed.state.toUpperCase();
 return {state,scale:stale?0:feed.scale,alert:stale||['STOP','LATCHED'].includes(state),metrics:`${Math.round((stale?0:feed.scale)*100)}% · nearest ${feed.nearest_m==null?'—':Number(feed.nearest_m).toFixed(3)+' m'} · ${feed.hands} hands · ${Number(feed.fps).toFixed(1)} fps · p50 ${Number(feed.latency_ms?.p50||0).toFixed(1)} / p95 ${Number(feed.latency_ms?.p95||0).toFixed(1)} ms · age ${Math.round(age*1000)} ms`};
}
export function csvRows(text){
 const rows=[];let row=[],cell='',quoted=false;
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}else if(!quoted&&(c===','||c==='\n')){row.push(cell.replace(/\r$/,''));cell='';if(c==='\n'){rows.push(row);row=[];}}else cell+=c;}
 if(quoted)throw Error('Unclosed CSV quotation');if(cell||row.length){row.push(cell.replace(/\r$/,''));rows.push(row);}return rows.filter(r=>r.some(v=>v.trim()));
}
export function parseTrace(text){
 if(text.length>5*1024**2)throw Error('Trace exceeds 5 MiB');const rows=csvRows(text);if(rows.length<2)throw Error('Trace needs a header and at least one sample');
 const head=rows.shift().map(s=>s.trim()),required=['t_s','move','joint','command_deg','observed_deg'],volts=head.filter(h=>h.startsWith('v_'));
 if(required.some(k=>!head.includes(k))||!volts.length||new Set(head).size!==head.length)throw Error('Missing or duplicate trace columns');
 let previous=-Infinity;const groups=[];
 for(const [i,row] of rows.entries()){
  if(row.length!==head.length)throw Error(`Wrong number of fields on row ${i+2}`);const r=Object.fromEntries(head.map((h,n)=>[h,row[n]]));
  for(const key of ['t_s','command_deg','observed_deg',...volts]){if(r[key].trim()===''||!Number.isFinite(Number(r[key])))throw Error(`Invalid ${key} on row ${i+2}`);r[key]=Number(r[key]);}
  if(r.t_s<0||r.t_s<previous)throw Error('Trace times must increase');previous=r.t_s;
  if(!r.move||!r.joint)throw Error('Trace needs move and joint labels');
  if(!groups.length||groups.at(-1).name!==r.move)groups.push({name:r.move,rows:[],volts});groups.at(-1).rows.push(r);
 }
 return groups;
}
const ns='http://www.w3.org/2000/svg';
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function svgEl(tag,attrs){const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);return e;}
function chart(group,axis){
 const wrap=element('section',undefined,'trace-chart'),svg=svgEl('svg',{viewBox:'0 0 680 240',role:'img','aria-label':`${group.name}: voltage against ${axis}`}),rows=group.rows;
 const xs=rows.map(r=>r[axis]),ys=rows.flatMap(r=>group.volts.map(k=>r[k])),lo=Math.min(...xs),hi=Math.max(...xs),ylo=Math.min(4.5,...ys)-.1,yhi=Math.max(5.5,...ys)+.1;
 const x=v=>55+(v-lo)/(hi-lo||1)*600,y=v=>200-(v-ylo)/(yhi-ylo)*165,colors=['#a24840','#3e6b99','#779927','#8456a7','#c87825','#297e78'];
 svg.append(svgEl('path',{d:'M55 25V200H655',fill:'none',stroke:'currentColor'}));
 const threshold=svgEl('line',{x1:55,x2:655,y1:y(4.5),y2:y(4.5),stroke:'#b33232','stroke-dasharray':'4 4'});svg.append(threshold);
 let minimum={v:Infinity};group.volts.forEach((k,i)=>{
  svg.append(svgEl('polyline',{points:rows.map(r=>`${x(r[axis])},${y(r[k])}`).join(' '),fill:'none',stroke:colors[i%colors.length],'stroke-width':1.5}));
  for(const r of rows)if(r[k]<minimum.v)minimum={v:r[k],row:r,k};
 });
 const txt=svgEl('text',{x:60,y:18,fill:'currentColor','font-size':12});txt.textContent=`${group.name} · volts / ${axis==='t_s'?'time (s)':'observed angle (°)'} · ${lo.toFixed(2)} → ${hi.toFixed(2)}`;svg.append(txt);
 svg.append(svgEl('circle',{cx:x(minimum.row[axis]),cy:y(minimum.v),r:4,fill:'#b33232'}));
 const label=svgEl('text',{x:60,y:225,fill:'currentColor','font-size':12});label.textContent=`Lowest: ${minimum.v.toFixed(2)} V · ${minimum.k.slice(2)} at ${minimum.row.t_s.toFixed(3)} s / ${minimum.row.observed_deg.toFixed(2)}°`;svg.append(label);
 const legend=element('p',undefined,'trace-legend');group.volts.forEach((k,i)=>{const label=element('span',k.slice(2));label.style.color=colors[i%colors.length];legend.append(label);});wrap.append(svg,legend);return wrap;
}
if(typeof document!=='undefined'){
 const $=id=>document.getElementById(id);let gate=null,preview=false,sampleMode=false,previewStep=0,previewTimer,pollBusy=false;
 function renderGate(){const view=gateView(gate);$('hg-state').textContent=view.state;$('hg-state').classList.toggle('instrument-alert',view.alert);$('hg-scale').value=view.scale;$('hg-metrics').textContent=view.metrics;$('hg-source').textContent=preview?'FAKE FEED PREVIEW / display only':gate?.external?'EXTERNAL HAND-GATE STATUS / display only':gate?.simulated?'SIMULATION GROUND TRUTH / no measured camera latency':'Live hand-gate display / missing or stale data cannot imply clearance';}
 async function pollGate(){if(pollBusy)return;pollBusy=true;try{if(preview&&gate)gate.updated=Date.now()/1000;if(!preview){try{const r=await fetch('/api/hand-gate',{signal:AbortSignal.timeout(1500)});gate=r.ok?await r.json():null;}catch{gate=null;}}renderGate();}finally{pollBusy=false;}}

 $('hg-preview').onclick=()=>{preview=!preview;clearInterval(previewTimer);$('hg-preview').textContent=preview?'Stop fake preview':'Preview all gate states';if(preview){const next=()=>{const state=['clear','slow','stop','latched','stale'][previewStep++%5];gate={state,scale:state==='clear'?1:state==='slow'?.4:0,nearest_m:.2,hands:1,fps:29,latency_ms:{p50:20,p95:28},stale_s:state==='stale'?.3:0,updated:Date.now()/1000};renderGate();};next();previewTimer=setInterval(next,900);}else{gate=null;pollGate();}};
 setInterval(renderGate,50);setInterval(pollGate,100);pollGate();
 async function api(path,body){const r=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(6000)}),d=await r.json();if(!r.ok)throw Error(d.error||'Telemetry unavailable');return d;}
 function renderArm(d){$('ra-state').textContent=d.state+(d.error?' · '+d.error:'');$('ra-connect').disabled=d.state==='LIVE / READ-ONLY';$('ra-joints').replaceChildren(...d.motors.map(r=>{const row=element('article',undefined,'joint-reading'),v=r.voltage_v;row.append(element('strong',r.joint),element('span',r.position_deg==null?'position unavailable':`${r.position_deg.toFixed(1)}° · Rest ${r.rest_deg}°`));const meter=element('meter');meter.min=r.range_deg?.[0]??-180;meter.max=r.range_deg?.[1]??180;meter.value=r.position_deg??0;meter.setAttribute('aria-label',r.joint+' position in calibrated range');const range=element('span',undefined,'joint-range');const rest=element('i',undefined,'joint-rest-marker');rest.title=`Rest ${r.rest_deg}°`;rest.style.left=`${Math.max(0,Math.min(100,(r.rest_deg-meter.min)/(meter.max-meter.min)*100))}%`;range.append(meter,rest);row.append(range,element('span',v==null?'voltage unavailable':`${v.toFixed(1)} V`,v!=null&&v<4.5?'instrument-alert':v!=null&&v<4.8?'instrument-warn':v!=null&&v>=5.2?'instrument-good':''),element('span',`${r.temperature_c??'—'} °C · torque ${r.torque_state??'unknown'}`),element('small',(r.errors||[]).join('; ')));return row;}));window.dispatchEvent(new CustomEvent('pathfinder-telemetry',{detail:d}));}
 $('ra-connect').onclick=async()=>{sampleMode=false;try{renderArm(await api('/api/real-arm/connect',{port:$('ra-port').value}));}catch(e){$('ra-state').textContent=e.message;}};
 $('ra-disconnect').onclick=async()=>{sampleMode=false;try{renderArm(await api('/api/real-arm/disconnect',{}));}catch(e){$('ra-state').textContent=e.message;}};
 $('ra-sample').onclick=async()=>{sampleMode=true;renderArm(await api('/api/real-arm/sample'));};
 setInterval(async()=>{if(sampleMode||($('real-arm').hidden&&!$('ws-live-joints').checked))return;try{renderArm(await api('/api/real-arm'));}catch(e){$('ra-state').textContent='NOT CONNECTED · '+e.message;}},1000);
 $('trace-sample').onclick=async()=>{try{const response=await fetch('/sample-trace.csv');if(!response.ok)throw Error('Sample unavailable');const groups=parseTrace(await response.text());$('trace-charts').replaceChildren(...groups.flatMap(g=>[chart(g,'t_s'),chart(g,'observed_deg')]));$('trace-status').textContent='2 move segments · FAKE BUS SAMPLE / no hardware connection';}catch(error){$('trace-status').textContent='Trace error: '+error.message;}};
 $('ra-trace').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;const groups=parseTrace(await file.text());$('trace-charts').replaceChildren(...groups.flatMap(g=>[chart(g,'t_s'),chart(g,'observed_deg')]));$('trace-status').textContent=`${groups.length} move segments · recorded trace / no hardware connection`;}catch(error){$('trace-charts').replaceChildren();$('trace-status').textContent='Trace error: '+error.message;}};
}
