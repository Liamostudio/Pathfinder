// Presentation only. Recovery decisions and motion always belong to the local runtime.
export function stopView(state){
 const stop=state?.stop;
 if(!state?.stopped&&!stop)return {title:'',reason:'',recovery:''};
 if(stop?.kind==='person')return {title:'STOP (you)',reason:String(stop.reason||'Stopped by you'),recovery:'Waiting for Carry on or reset. No automatic recovery.'};
 if(stop?.kind==='fault'){
  const left=Number.isInteger(stop.resets_left)&&stop.resets_left>=0?stop.resets_left:null;
  const seconds=typeof stop.recovering_in_s==='number'&&Number.isFinite(stop.recovering_in_s)&&stop.recovering_in_s>=0?stop.recovering_in_s:null;
  const recovery=left===0?'Automatic recovery limit reached. Check the fault, then use Carry on.':seconds!==null?`Recovery check in ${seconds.toFixed(1)} s${left===null?'':` · ${left} automatic resets left`}`:`Waiting for healthy cameras and a clear path${left===null?'':` · ${left} automatic resets left`}`;
  return {title:'FAULT STOP',reason:String(stop.reason||'Runtime fault'),recovery};
 }
 return {title:'STOPPED',reason:'Stop details unavailable',recovery:'Check the local runtime before continuing.'};
}
export function routePoints(route,start=null){
 if(!['over','around','hand'].includes(route?.kind)||!Array.isArray(route.waypoints)||!route.waypoints.length||route.waypoints.length>128)return [];
 const valid=p=>Array.isArray(p)&&p.length===3&&p.every(v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=10);
 if(!route.waypoints.every(valid))return [];
 const points=route.waypoints.map(p=>[...p]);if(valid(start)&&points[0].some((v,i)=>v!==start[i]))points.unshift([...start]);return points;
}
export function routeFrame(previous,route,start){
 const points=routePoints(route,start),key=points.length?JSON.stringify([route.kind,route.waypoints]):null;
 return previous?.key===key?previous:{key,origin:points.length?[...points[0]]:null};
}
export function routeVertices(route,start,alignment,scale=1){
 const m=alignment?.canonical_from_marker;
 if(!Array.isArray(m)||m.length!==4||!m.every(r=>Array.isArray(r)&&r.length===4&&r.every(Number.isFinite))||m[3].some((v,i)=>v!==[0,0,0,1][i])||!Number.isFinite(scale)||scale<=0)return new Float32Array();
 const values=[];for(const p of routePoints(route,start)){const q=m.slice(0,3).map(r=>(r.slice(0,3).reduce((s,v,i)=>s+v*p[i],r[3]))*scale);values.push(...q,.84,.99,.32,0,0,1);}return new Float32Array(values);
}
export const isArmEvent=event=>['arm','safety'].includes(String(event.source).toLowerCase());
export function shortStatus(events=[]){const event=[...events].reverse().find(isArmEvent);return event?String(event.output||event.input||'').slice(0,240):'';}
export function schematicRoute(route,start,top=false){return routePoints(route,start).map(([x,y,z])=>top?[480+x*760,280-y*680]:[440+x*680+y*380,330+x*180-y*300-z*650]);}

if(typeof document!=='undefined'){
 const $=id=>document.getElementById(id);let state=null,controlBusy=false,lastUpdate=0,routeState=null;
 function render(){
  if(!state)return;const stale=Date.now()-lastUpdate>2500,view=stopView(state);
  $('motion-stop').hidden=!view.title;$('motion-stop-kind').textContent=view.title;$('motion-stop-reason').textContent=view.reason;$('motion-recovery').textContent=stale&&view.title?'Runtime updates lost · recovery status unavailable':view.recovery;
  if(typeof state.stop_distance_m==='number'&&Number.isFinite(state.stop_distance_m))$('motion-stop-distance').textContent=state.stop_distance_m.toFixed(2);
  if(view.title){$('motion-overlay-title').textContent=view.title;$('motion-overlay-reason').textContent=view.reason+' · '+(state.stop?.kind==='fault'?'Waiting for recovery checks.':'Use Carry on or reset to continue.');$('gate-status').textContent=view.title;$('speed-caption').textContent=state.stop?.kind==='fault'?'fault stop':state.stop?.kind==='person'?'stopped by you':'stop latched';}
  $('motion-status').textContent=shortStatus(state.events)|| (state.busy?'Instruction running':'');
  $('motion-cancel').disabled=controlBusy||stale||state.backend!=='sim';$('motion-resume').disabled=controlBusy||stale||state.backend!=='sim'||!state.stopped||state.motion_controls?.resume!==true;
  const points=schematicRoute(state.route,routeState?.origin,document.querySelector('[data-view="top"]')?.classList.contains('active'));
  const svg=$('motion-route');svg.replaceChildren();svg.toggleAttribute('hidden',!points.length||stale);
  if(points.length&&!stale){const ns='http://www.w3.org/2000/svg',line=document.createElementNS(ns,'polyline');line.setAttribute('points',points.map(p=>p.join(',')).join(' '));svg.append(line);for(const p of points){const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);dot.setAttribute('r',5);svg.append(dot);}}
  $('motion-route-label').textContent=points.length&&!stale?`Planned simulator route · ${state.route.kind==='over'?'over no-go cubes':state.route.kind==='hand'?'around a hand':'around no-go cubes'}`:'';
 }
 async function control(text){
  if(controlBusy)return;controlBusy=true;render();
  try{const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:AbortSignal.timeout(6000)}),result=await response.json();if(!response.ok)throw Error(result.error||'Control refused');$('motion-control-status').textContent=text==='cancel'?'Cancel requested':'Carry on requested';}
  catch(e){$('motion-control-status').textContent=e.message;}
  finally{controlBusy=false;render();}
 }
 $('motion-cancel').onclick=()=>control('cancel');$('motion-resume').onclick=()=>control('carry on');
 document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>render()));
 window.addEventListener('pathfinder-state',e=>{state=e.detail;routeState=routeFrame(routeState,state.route,state.scene?.arm_position);lastUpdate=Date.now();render();});
 const timer=setInterval(render,500);window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
}
