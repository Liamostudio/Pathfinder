import {frameIndex, nearestEvent, latestEvent, scrubTime, advanceTime} from './playback.mjs';
const $ = id => document.getElementById(id);
const canvas = $('scene'), ctx = canvas.getContext('2d');
let take, time = 0, duration = 0, playing = false, previousTick = null, activeEvent = -1;
const format = t => `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;
const content = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const clockText = t => `${format(t)}.${String(Math.floor((t%1)*10))}`;
function line(points, colour, width = 2, dash = []) {
  if (!points.length) return;
  ctx.beginPath(); ctx.setLineDash(dash); ctx.strokeStyle=colour; ctx.lineWidth=width;
  points.forEach(([x,y],i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y)); ctx.stroke(); ctx.setLineDash([]);
}
function disc(p, r, colour) { ctx.beginPath(); ctx.arc(p[0],p[1],r,0,Math.PI*2);ctx.fillStyle=colour;ctx.fill(); }
// Renderer boundary: replace this function to introduce a 3D view later.
// The two links are a schematic from arm_position, not reconstructed joint geometry.
export function render(frame) {
  const box=canvas.getBoundingClientRect(), ratio=window.devicePixelRatio||1;
  if(canvas.width!==Math.round(box.width*ratio)||canvas.height!==Math.round(box.height*ratio)) {
    canvas.width=Math.round(box.width*ratio);canvas.height=Math.round(box.height*ratio);
  }
  ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,box.width,box.height);
  const availableHeight=box.height-150, scale=Math.min((box.width-65)/1.05,(availableHeight-20)/.4);
  const origin=[box.width*.42,availableHeight*.56+20];
  const point=p=>[origin[0]+p[0]*scale,origin[1]-p[1]*scale];
  const tl=point([-.36,.30]), br=point([.62,-.30]);
  ctx.fillStyle='#202d23';ctx.fillRect(tl[0],tl[1],br[0]-tl[0],br[1]-tl[1]);
  for(let x=-.3;x<=.6;x+=.1)line([point([x,-.3]),point([x,.3])],'#344339',1);
  for(let y=-.3;y<=.3;y+=.1)line([point([-.36,y]),point([.62,y])],'#344339',1);
  ctx.fillStyle='#0d140e';ctx.fillRect(origin[0]-15,origin[1]-15,30,30);
  ctx.strokeStyle='#ebf2df';ctx.lineWidth=2;ctx.strokeRect(origin[0]-11,origin[1]-11,22,22);
  ctx.fillStyle='#ebf2df';ctx.fillRect(origin[0]-7,origin[1]-7,7,7);ctx.fillRect(origin[0]+1,origin[1]+1,6,6);
  ctx.font='11px system-ui';ctx.fillStyle='#b6c6b8';ctx.fillText('MARKER / 0, 0',origin[0]-38,origin[1]+32);
  line([[20,24],[20+.1*scale,24]],'#aab8ad',2);ctx.fillText('10 cm',20,42);
  const tip=point(frame.scene.arm_position);
  if(frame.route?.waypoints?.length)line([tip,...frame.route.waypoints.map(point)],'#8cc5f1',3,[6,5]);
  for(const hand of frame.scene.hands) {
    const p=point(hand.position),r=Math.max(8,hand.radius_m*scale);
    disc(p,r,'#ff9a8638');ctx.strokeStyle='#ff9a86';ctx.lineWidth=2;ctx.stroke();
    ctx.fillStyle='#ffd4c9';ctx.fillText('HAND',p[0]-16,p[1]-r-8);
  }
  const base=point([-.12,-.02]);
  const elbow=[(base[0]+tip[0])/2-20,(base[1]+tip[1])/2-28];
  line([base,elbow,tip],'#0d140e',19);line([base,elbow,tip],'#d4f879',11);
  disc(base,13,'#78914d');disc(base,6,'#d4f879');disc(elbow,8,'#d4f879');
  ctx.strokeStyle='#d4f879';ctx.lineWidth=3;ctx.strokeRect(tip[0]-9,tip[1]-9,18,18);
  for(const object of frame.scene.objects) {
    const p=object.held?tip:point(object.position);
    ctx.fillStyle=/red/i.test(object.label)?'#f58979':/blue/i.test(object.label)?'#86bff3':'#e6d591';
    ctx.fillRect(p[0]-8,p[1]-8,16,16);ctx.fillStyle='#e8efdf';
    ctx.fillText(`${object.label}${object.held?' · held':''}`,p[0]+13,p[1]+4);
  }
  ctx.fillStyle='#aab8ad';ctx.fillText('x →  /  y ↑   ·   arm schematic',18,availableHeight+2);
}
function captionAt(t, frame) {
  const command=latestEvent(take.events,t,e=>['Meta glasses','Muse Voice Transcribe'].includes(e.source));
  const model=latestEvent(take.events,t,e=>/Muse Spark|scripted model/i.test(e.source));
  $('command').textContent=command?content(command.output):model?content(model.input):'No spoken command recorded yet';
  $('command-label').textContent=command?command.source.toUpperCase():model?'SCRIPTED COMMAND':'COMMAND';
  $('answer-label').textContent=model?model.source.toUpperCase():'MODEL RESPONSE';
  $('answer').textContent=model?content(model.output):'No model response recorded yet';
  const arm=latestEvent(take.events,t,e=>e.source==='Arm');
  const state=frame.hand_gate?.state;
  $('arm').textContent=frame.stop?.reason|| (frame.stopped?'Stop latched':state==='stop'?'Pausing: a hand is in the way':arm?content(arm.output):'Following the recorded motion');
}
function update() {
  if(!take)return;
  const frame=take.frames[frameIndex(take.frames,time)];render(frame);captionAt(time,frame);
  $('clock').textContent=`${format(time)} / ${format(duration)}`;$('scrub').value=time;
  const gate=frame.hand_gate||{}, scale=frame.stopped?0:frame.speed_scale;
  $('gate').textContent=gate.state||'Unknown';$('gauge').value=scale;$('scale').textContent=`${Math.round(scale*100)}% speed`;
  $('separation').textContent=gate.nearest_m==null?'No measurement':`${gate.nearest_m.toFixed(3)} m separation`;
  const stop=frame.stop, handStop=gate.state==='stop';
  $('stop').hidden=!stop&&!frame.stopped&&!handStop;
  $('stop').dataset.kind=stop?.kind||'hand';
  $('stop').textContent=stop?`${stop.kind==='person'?'PERSON STOP':stop.kind==='fault'?'FAULT STOP':'STOP'} · ${stop.reason}${stop.kind==='fault'&&stop.recovering_in_s!=null?` · Recovery in ${stop.recovering_in_s.toFixed(1)} s · ${stop.resets_left} resets left`:''}`:frame.stopped?'STOP LATCHED · Replay paused motion': 'HAND STOP · Pausing: a hand is in the way';
  const next=nearestEvent(take.events,time);
  if(next!==activeEvent) {
    if(activeEvent>=0){$('event-'+activeEvent).classList.remove('active');$('event-'+activeEvent).firstElementChild.removeAttribute('aria-current');}
    activeEvent=next;
    if(next>=0) {
      const el=$('event-'+next);el.classList.add('active');el.firstElementChild.setAttribute('aria-current','true');
      // Scroll only the timeline, never move the page under the playback controls.
      const list=$('timeline');list.scrollTop=Math.max(0,el.offsetTop-list.offsetTop-list.clientHeight*.25);
    }
  }
}
function setPlaying(value) {playing=value;previousTick=null;$('play').textContent=value?'Pause':'Play';$('play').setAttribute('aria-label',`${value?'Pause':'Play'} recorded take`);}
function seek(value) {time=scrubTime(value,duration);previousTick=null;update();}
$('play').addEventListener('click',()=>{if(time>=duration)seek(0);setPlaying(!playing);});
$('restart').addEventListener('click',()=>{setPlaying(false);seek(0);});
$('scrub').addEventListener('input',e=>seek(e.target.value));
$('speed').addEventListener('change',()=>{previousTick=null;});
new ResizeObserver(()=>update()).observe(canvas);
function tick(now) {
  if(playing&&take) {
    if(previousTick!==null) time=advanceTime(time,(now-previousTick)/1000,Number($('speed').value),duration);
    previousTick=now;if(time>=duration)setPlaying(false);update();
  }
  requestAnimationFrame(tick);
}
document.addEventListener('visibilitychange',()=>{previousTick=null;});
requestAnimationFrame(tick);
async function load() {
  try {
    const response=await fetch('./data/take.json');if(!response.ok)throw Error(`Take unavailable (${response.status})`);
    take=await response.json();
    if(take.schema_version!==1||!take.frames?.length||!Array.isArray(take.events))throw Error('Invalid take. Export it again with scripts/export_replay.py.');
    duration=take.frames.at(-1).t;
    $('title').textContent=take.title;$('duration').textContent=`${duration.toFixed(1)} seconds · ${take.frames.length} recorded frames`;
    $('cost').textContent=`${take.events.length} events · $${take.events.reduce((n,e)=>n+(e.cost_usd||0),0).toFixed(3)} recorded cost`;
    $('event-count').textContent=`${take.events.length} EVENTS`;
    for(const limitation of take.limitations||[]) {const li=document.createElement('li');li.textContent=limitation;$('limitations').append(li);}
    take.events.forEach((event,i)=>{
      const row=document.createElement('div');row.className='event';row.id='event-'+i;row.setAttribute('role','listitem');
      const button=document.createElement('button');button.type='button';
      const head=document.createElement('div');head.className='event-head';
      const source=document.createElement('strong');source.textContent=event.source;const stamp=document.createElement('time');stamp.textContent=clockText(event.t);head.append(source,stamp);button.append(head);
      for(const key of ['input','output']) {const p=document.createElement('p'),label=document.createElement('span');label.textContent=key.toUpperCase();p.append(label,document.createTextNode(content(event[key])));button.append(p);}
      const meta=document.createElement('small');meta.textContent=`${event.latency_ms.toFixed(1)} ms · $${(event.cost_usd||0).toFixed(3)} · ${event.status}`;button.append(meta);
      button.addEventListener('click',()=>{setPlaying(false);seek(event.t);});row.append(button);$('timeline').append(row);
    });
    for(const id of ['play','restart','scrub'])$(id).disabled=false;
    $('scrub').max=duration;update();
  } catch(error) {$('error').hidden=false;$('error').textContent=`Unable to load the replay: ${error.message}`;$('duration').textContent='Take unavailable';}
}
load();
