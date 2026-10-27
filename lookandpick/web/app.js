'use strict';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let state = null;
let connected = false;
let pending = false;
let demoRunning = false;
let demoGeneration = 0;
let filter = 'all';
let page = 'workspace';
let viewpoint = 'perspective';
let media = 'synthetic';
let stream = null;
let recordingUrl = null;
let toastTimer;
let eventSignature = '';
let objectSignature = '';
let visualArm = [0, 0, 0.2];
let sceneSignature = '';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5500);
}

function connection(ok) {
  connected = ok;
  $('#connection-label').textContent = ok ? 'local simulator' : 'server disconnected';
  $('#connection-led').classList.toggle('offline', !ok);
  updateButtons();
}

async function request(action, fields = {}, quiet = false) {
  if (!quiet) pending = true;
  updateButtons();
  try {
    const response = await fetch('/api/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...fields }), signal: AbortSignal.timeout(6000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The action could not be completed');
    connection(true);
    acceptState(data);
    if (!quiet && ['pick', 'place', 'command'].includes(action)) {
      toast(data.events[data.events.length - 1].output);
    }
    return data;
  } catch (error) {
    if (error.name === 'TypeError' || error.name === 'TimeoutError') connection(false);
    toast(error.message);
    throw error;
  } finally {
    if (!quiet) pending = false;
    updateButtons();
  }
}

async function poll() {
  try {
    if (!pending) {
      const response = await fetch('/api/state', { signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error('Session unavailable');
      acceptState(await response.json());
      connection(true);
    }
  } catch (_) { connection(false); }
  setTimeout(poll, 600);
}

function updateButtons() {
  const stopped = state?.stopped || !connected;
  const busy = pending || demoRunning;
  $('#pick').disabled = stopped || busy || !!state?.gripping;
  $('#place').disabled = stopped || busy || !state?.gripping;
  $('#run-demo').disabled = !connected || pending || demoRunning;
  $('#reset').disabled = !connected || pending;
  $('#command').disabled = stopped || busy;
  $('.command-send').disabled = stopped || busy;
  $$('#objects button, [data-hand]').forEach(button => { button.disabled = stopped || busy; });
  $('#run-demo').textContent = demoRunning ? '▶ demo running…' : '▶ play demo';
}

function acceptState(next) {
  if (state && next.instance_id === state.instance_id && next.revision < state.revision) return;
  state = next;
  const selected = state.scene.objects.find(object => object.id === state.selected);
  $('#speed').textContent = Math.round(state.speed_scale * 100);
  $('#speed-fill').style.width = `${state.speed_scale * 100}%`;
  const gate = state.stopped ? 'stop latched' : state.speed_scale === 0 ? 'blocked' : state.speed_scale < 1 ? 'slowing' : 'clear';
  $('#gate-status').textContent = gate;
  $('#gate-status').classList.toggle('blocked', state.stopped || state.speed_scale === 0);
  $('#speed-caption').textContent = state.stopped ? 'manual stop' : state.speed_scale === 0 ? 'hand too close' : state.speed_scale < 1 ? 'hand nearby' : 'path clear';
  $('#separation').textContent = state.separation_m === null ? 'clear' : `${state.separation_m.toFixed(2)} m`;
  $('#gripper').textContent = `gripper / ${state.gripping ? 'closed' : 'open'}`;
  $('#action-title').replaceChildren(document.createTextNode(state.gripping ? 'holding ' : 'pick '));
  const targetLabel = document.createElement('span');
  const held = state.scene.objects.find(object => object.held);
  targetLabel.textContent = `${(held || selected).label}.`;
  $('#action-title').append(targetLabel);
  $('#action-detail').textContent = state.gripping ? 'select the held object, then place' : 'hover → descend → grip → lift';
  $('#stop-overlay').hidden = !state.stopped;
  $('#stop').classList.toggle('latched', state.stopped);
  const seconds = Math.floor(state.uptime_s);
  $('#session-time').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  $$('[data-hand]').forEach(button => {
    const active = button.dataset.hand === state.hand_mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active);
  });
  renderObjects();
  renderEvents();
  updateButtons();
}

const icons = {
  obj1: '<path d="m12 2 10 6v12l-10 6L2 20V8Zm0 12 10-6M12 14 2 8m10 6v12"/>',
  obj2: '<path d="M4 5h15v15c0 5-15 5-15 0Zm15 3h3c6 0 6 10 0 10h-3M4 5c0 4 15 4 15 0"/>',
};

function renderObjects() {
  const signature = JSON.stringify([state.selected, state.scene.objects]);
  if (objectSignature === signature) return;
  objectSignature = signature;
  const focusedId = document.activeElement?.dataset?.object;
  $('#objects').replaceChildren(...state.scene.objects.map(object => {
    const button = document.createElement('button');
    button.className = `object-card${state.selected === object.id ? ' selected' : ''}`;
    button.dataset.object = object.id;
    button.setAttribute('aria-pressed', state.selected === object.id);
    const icon = document.createElement('span');
    icon.className = 'object-icon';
    icon.innerHTML = `<svg viewBox="0 0 28 28" aria-hidden="true">${icons[object.id] || icons.obj1}</svg>`;
    const content = document.createElement('span');
    const label = document.createElement('strong');
    label.textContent = object.label;
    const coordinates = document.createElement('small');
    coordinates.textContent = `${object.held ? 'held / ' : ''}${object.position[0].toFixed(2)}, ${object.position[1].toFixed(2)}, ${object.position[2].toFixed(2)} m`;
    content.append(label, coordinates);
    const check = document.createElement('span');
    check.className = 'object-check';
    check.textContent = state.selected === object.id ? '✓' : '';
    button.append(icon, content, check);
    button.addEventListener('click', () => request('select', { id: object.id }).catch(() => {}));
    return button;
  }));
  if (focusedId) $(`[data-object="${focusedId}"]`)?.focus({ preventScroll: true });
}

function renderEvents() {
  const events = state.events.filter(event => filter === 'all' || (filter === 'models' ? event.scripted : ['tools', 'safety'].includes(event.source)));
  const signature = JSON.stringify([events, filter]);
  if (signature === eventSignature) return;
  eventSignature = signature;
  $('#event-count').textContent = String(state.events.length).padStart(2, '0');
  if (!events.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-log';
    empty.textContent = filter === 'models' ? 'No model cues yet. Play the demo to see a labelled scripted example. No model API is connected.' : 'No matching events yet. Pick a target or try a hand scenario.';
    $('#event-list').replaceChildren(empty);
    return;
  }
  $('#event-list').replaceChildren(...[...events].reverse().map(event => {
    const row = document.createElement('div');
    row.className = `event-row ${event.status}`;
    const stamp = document.createElement('span');
    stamp.className = 'event-time';
    stamp.textContent = new Date(event.time).toLocaleTimeString('en-GB');
    const source = document.createElement('span');
    source.className = 'event-source';
    source.textContent = event.source;
    if (event.scripted) { const tag = document.createElement('small'); tag.textContent = 'scripted'; source.append(tag); }
    const copy = document.createElement('div');
    copy.className = 'event-copy';
    const input = document.createElement('strong');
    input.textContent = event.input;
    const output = document.createElement('p');
    output.textContent = event.output;
    copy.append(input, output);
    const latency = document.createElement('span');
    latency.className = 'event-latency';
    latency.textContent = event.scripted ? 'demo cue' : `${event.latency_ms.toFixed(1)} ms`;
    row.append(stamp, source, copy, latency);
    return row;
  }));
}

function project(point) {
  const [x, y, z = 0] = point;
  return viewpoint === 'top' ? [480 + x * 760, 280 - y * 680] : [440 + x * 680 + y * 380, 330 + x * 180 - y * 300 - z * 650];
}
const points = list => list.map(point => project(point).map(n => n.toFixed(2)).join(',')).join(' ');
function line(a, b, attributes = '') {
  const p = project(a), q = project(b);
  return `<line x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}" ${attributes}/>`;
}
function label(point, text, selected = false) {
  const [x, y] = project(point);
  return `<g transform="translate(${x - 43},${y - 44})"><rect width="105" height="23" rx="5" fill="${selected ? '#D5FD51' : '#252824'}" stroke="${selected ? '#D5FD51' : '#757c70'}" stroke-width=".7"/><text x="9" y="15" fill="${selected ? '#161616' : '#f2f2ef'}" font-family="Space Mono,monospace" font-size="9">${text}</text></g>`;
}

function drawScene() {
  if (state && media === 'synthetic') {
    const target = state.scene.arm_position;
    visualArm = visualArm.map((n, i) => reducedMotion ? target[i] : n + (target[i] - n) * 0.1);
    const signature = JSON.stringify([viewpoint, state.scene, state.selected, visualArm.map(n => n.toFixed(4))]);
    if (signature === sceneSignature) {
      requestAnimationFrame(drawScene);
      return;
    }
    sceneSignature = signature;
    const selected = state.scene.objects.find(object => object.id === state.selected);
    let svg = '<defs><pattern id="floor-dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".65" fill="#62695d" opacity=".35"/></pattern><filter id="soft-shadow"><feGaussianBlur stdDeviation="7"/></filter></defs><rect width="960" height="560" fill="url(#floor-dots)"/>';
    svg += `<polygon points="${points([[-.35, -.35], [.5, -.35], [.5, .36], [-.35, .36]])}" fill="#a9ada3" stroke="#c9cdc0" stroke-width="1.5"/>`;
    for (let x = -.3; x <= .501; x += .05) svg += line([x, -.35], [x, .36], 'stroke="#7d8574" stroke-width=".7" opacity=".35"');
    for (let y = -.3; y <= .351; y += .05) svg += line([-.35, y], [.5, y], 'stroke="#7d8574" stroke-width=".7" opacity=".35"');
    const zone = state.drop_zone;
    const zx = zone[0], zy = zone[1];
    svg += `<polygon points="${points([[zx-.075,zy-.065],[zx+.075,zy-.065],[zx+.075,zy+.065],[zx-.075,zy+.065]])}" fill="#e0e3d7" fill-opacity=".3" stroke="#515b46" stroke-width="1" stroke-dasharray="5 4"/>`;
    const zp = project([zx, zy, 0]);
    svg += `<text x="${zp[0]-28}" y="${zp[1]+40}" fill="#424b39" font-family="Space Mono,monospace" font-size="9">drop zone</text>`;
    svg += line([0,0,.002], [.10,0,.002], 'stroke="#4d5644" stroke-width="1.5"') + line([0,0,.002], [0,.10,.002], 'stroke="#4d5644" stroke-width="1.5"');
    const axis = project([.11, 0]);
    svg += `<text x="${axis[0]}" y="${axis[1]+3}" fill="#4d5644" font-family="Space Mono,monospace" font-size="9">x</text>`;
    svg += `<polygon points="${points([[-.035,-.035], [.035,-.035], [.035,.035], [-.035,.035]])}" fill="#292e26" stroke="#e3e6db" stroke-width="1"/>`;
    svg += `<polygon points="${points([[-.018,-.018], [.018,-.018], [.018,.018], [-.018,.018]])}" fill="#d2d7c6"/>`;
    svg += line(visualArm, [...selected.position.slice(0,2),.025], 'stroke="#e9eddf" stroke-opacity=".8" stroke-width="1.5" stroke-dasharray="6 6"');
    state.scene.objects.forEach(object => {
      const [x,y,z] = object.held ? visualArm : object.position;
      const selectedObject = object.id === state.selected;
      const p = project([x,y,z]);
      if (object.id === 'obj1') {
        const h = .045, r = .027;
        svg += `<polygon points="${points([[x-r,y-r,z],[x+r,y-r,z],[x+r,y-r,z+h],[x-r,y-r,z+h]])}" fill="#9e5149" stroke="#633c35" stroke-width="1"/><polygon points="${points([[x+r,y-r,z],[x+r,y+r,z],[x+r,y+r,z+h],[x+r,y-r,z+h]])}" fill="#b76255" stroke="#633c35" stroke-width="1"/><polygon points="${points([[x-r,y-r,z+h],[x+r,y-r,z+h],[x+r,y+r,z+h],[x-r,y+r,z+h]])}" fill="#d17a6b" stroke="#633c35" stroke-width="1"/>`;
      } else {
        svg += `<ellipse cx="${p[0]}" cy="${p[1]}" rx="20" ry="8" fill="#35484f"/><path d="M${p[0]-20} ${p[1]-26}v26c0 12 40 12 40 0v-26" fill="#64828a" stroke="#3a545c" stroke-width="1.5"/><ellipse cx="${p[0]}" cy="${p[1]-26}" rx="20" ry="8" fill="#9aadb0" stroke="#3a545c" stroke-width="1.5"/><ellipse cx="${p[0]}" cy="${p[1]-26}" rx="14" ry="5" fill="#40585c"/><path d="M${p[0]+20} ${p[1]-20}c23-3 25 20 0 20" stroke="#54747e" stroke-width="7" fill="none"/>`;
      }
      if (selectedObject) svg += `<rect x="${p[0]-33}" y="${p[1]-43}" width="66" height="57" rx="7" fill="#D5FD51" fill-opacity=".07" stroke="#D5FD51" stroke-width="1.5" stroke-dasharray="7 4"/>`;
      svg += label([x,y,z+.04], `${object.id} / ${object.held ? 'held' : object.id === 'obj1' ? 'cube' : 'mug'}`, selectedObject);
    });
    const base = project([0,0,.025]);
    svg += `<ellipse cx="${base[0]}" cy="${base[1]+5}" rx="50" ry="19" fill="#252a21" opacity=".3" filter="url(#soft-shadow)"/><ellipse cx="${base[0]}" cy="${base[1]}" rx="33" ry="15" fill="#30352b" stroke="#4c5345" stroke-width="3"/><ellipse cx="${base[0]}" cy="${base[1]-6}" rx="30" ry="12" fill="#dde1d3" stroke="#747e68" stroke-width="2"/>`;
    const shoulder = [0,0,.08], elbow = [-.13,.025,.30];
    const wrist = [visualArm[0]-.035,visualArm[1],visualArm[2]+.055];
    for (const [a,b,width] of [[shoulder,elbow,23],[elbow,wrist,17],[wrist,visualArm,10]]) {
      svg += line(a,b,`stroke="#252a22" stroke-width="${width+5}" stroke-linecap="round"`);
      svg += line(a,b,`stroke="#d1d7c4" stroke-width="${width}" stroke-linecap="round"`);
      svg += line(a,b,`stroke="#f0f3e9" stroke-width="2" stroke-linecap="round" opacity=".6"`);
    }
    for (const joint of [shoulder,elbow,wrist]) {
      const p=project(joint);
      svg += `<circle cx="${p[0]}" cy="${p[1]}" r="12" fill="#252a22" stroke="#bcc5b0" stroke-width="3"/><circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#7d8972"/>`;
    }
    const end = project(visualArm), gripWidth = state.gripping ? 7 : 17;
    svg += `<path d="M${end[0]-gripWidth} ${end[1]+8}v-12h${gripWidth*2}v12" fill="none" stroke="#252a22" stroke-width="5" stroke-linejoin="round"/>`;
    state.scene.hands.forEach(hand => {
      const p = project(hand.position), color = state.speed_scale === 0 ? '#f2f2ef' : '#D5FD51';
      svg += `<circle cx="${p[0]}" cy="${p[1]}" r="55" fill="${color}" fill-opacity=".08" stroke="${color}" stroke-opacity=".5" stroke-dasharray="4 5"/><g transform="translate(${p[0]-16} ${p[1]-25})"><path d="M5 35v-20c0-6 7-6 7 0V4c0-6 7-6 7 0v12V1c0-5 7-5 7 0v16V7c0-5 7-5 7 0v29c0 18-25 24-33 7l-8-12c-3-5 2-9 6-5l7 9Z" fill="#8a9280" stroke="${color}" stroke-width="2"/></g>`;
      svg += label([hand.position[0],hand.position[1],hand.position[2]+.05], 'synthetic hand');
    });
    $('#scene-svg').innerHTML = svg;
    $('#stage-coordinate').textContent = state.scene.arm_position.map((n,i) => `${'xyz'[i]} ${n>=0?'+':''}${n.toFixed(3)}`).join(' / ');
  }
  requestAnimationFrame(drawScene);
}

function showPage(next) {
  page = next;
  $('#workspace').hidden = next !== 'workspace';
  $('#calibration').hidden = next !== 'calibration';
  $('#timeline').hidden = next === 'calibration';
  $$('[data-page]').forEach(button => {
    button.classList.toggle('active', button.dataset.page === page);
    if (button.dataset.page === page) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
}

function stopMedia() {
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null;
  $('#camera-video').srcObject = null;
  $('#recording-video').pause();
  $('#recording-video').removeAttribute('src');
  $('#recording-video').load();
  if (recordingUrl) URL.revokeObjectURL(recordingUrl);
  recordingUrl = null;
}

function showMedia(next) {
  media = next;
  $('#scene-svg').toggleAttribute('hidden', next !== 'synthetic');
  $('#camera-video').hidden = next !== 'camera';
  $('#recording-video').hidden = next !== 'recording';
  $('#stage-coordinate').hidden = next !== 'synthetic';
  $('#synthetic').hidden = next === 'synthetic';
  $('.view-switch').hidden = next !== 'synthetic';
  $('#stage-label').textContent = next === 'synthetic' ? '● synthetic scene' : next === 'camera' ? '● webcam preview' : '● local recording';
  $('#media-mode').textContent = next === 'synthetic' ? 'SIMULATED WORKSPACE' : 'PREVIEW ONLY / NO DETECTION';
  $('#stage-subtitle').textContent = next === 'synthetic' ? 'illustrative motion · no camera input' : 'robot controls continue to use synthetic state';
  $('#preview-note').textContent = next === 'synthetic' ? 'A schematic of the shared world state. Webcam and recording previews are optional; neither runs detection.' : 'Preview stays on this device. No detection or calibration is applied. The safety gate and robot controls use the synthetic scene.';
}

async function runDemo() {
  const generation = ++demoGeneration;
  demoRunning = true;
  updateButtons();
  stopMedia(); showMedia('synthetic'); showPage('workspace');
  const step = async (action, fields = {}) => {
    if (generation !== demoGeneration) throw new Error('Demo interrupted');
    return request(action, fields, true);
  };
  const pause = () => new Promise(resolve => setTimeout(resolve, reducedMotion ? 200 : 1600));
  try {
    await step('reset');
    toast('Demo: select a target, test the hand gate, then pick and place.');
    await pause(); await step('cue');
    await pause(); await step('hand', { mode: 'approach' });
    await pause(); await step('hand', { mode: 'near' });
    await pause(); await step('pick');
    await pause(); await step('hand', { mode: 'clear' });
    await pause(); await step('pick');
    await pause(); await step('place');
    toast('Demo complete. The local tools blocked the unsafe pick, then completed a clear pick and place.');
  } catch (error) {
    if (error.message !== 'Demo interrupted') toast(error.message);
  } finally {
    if (generation === demoGeneration) {
      demoRunning = false;
      updateButtons();
    }
  }
}

async function stop() {
  ++demoGeneration;
  demoRunning = false;
  await request('stop', {}, true);
  toast('Stop latched. Start a new simulation to reset.');
}

$('#pick').addEventListener('click', () => request('pick').catch(() => {}));
$('#place').addEventListener('click', () => request('place').catch(() => {}));
$('#stop').addEventListener('click', () => stop().catch(() => {}));
$('#reset').addEventListener('click', () => { ++demoGeneration; demoRunning = false; request('reset').then(() => toast('New simulation ready.')).catch(() => {}); });
$('#run-demo').addEventListener('click', runDemo);
$$('[data-hand]').forEach(button => button.addEventListener('click', () => request('hand', { mode: button.dataset.hand }).catch(() => {})));
$$('[data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
$$('[data-view]').forEach(button => button.addEventListener('click', () => {
  viewpoint = button.dataset.view;
  $$('[data-view]').forEach(item => { const active = item === button; item.classList.toggle('active',active); item.setAttribute('aria-pressed',active); });
}));
$$('[data-filter]').forEach(button => button.addEventListener('click', () => {
  filter = button.dataset.filter;
  $$('[data-filter]').forEach(item => item.classList.toggle('active',item === button));
  if (state) renderEvents();
}));
$('#command-form').addEventListener('submit', event => {
  event.preventDefault();
  const text = $('#command').value.trim();
  if (text && !pending && !demoRunning && !state?.stopped) request('command', { text }).then(() => { $('#command').value = ''; }).catch(() => {});
});
document.addEventListener('keydown', event => {
  if (event.code === 'Space' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName) && !document.activeElement?.isContentEditable && !event.repeat) {
    event.preventDefault(); stop().catch(() => {});
  }
}, true);
function setTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('#theme').setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  try { localStorage.setItem('pathfinder-theme', dark ? 'dark' : 'light'); } catch (_) { /* storage may be disabled */ }
}
$('#theme').addEventListener('click', () => setTheme(document.documentElement.dataset.theme !== 'dark'));
try { setTheme(localStorage.getItem('pathfinder-theme') === 'dark'); } catch (_) { setTheme(false); }

$('#camera').addEventListener('click', async () => {
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('This browser does not provide webcam access. Try opening the local URL in your regular browser.');
    stopMedia();
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    $('#camera-video').srcObject = stream;
    showMedia('camera');
  } catch (error) { showMedia('synthetic'); toast(`Webcam unavailable: ${error.message}`); }
});
$('#synthetic').addEventListener('click', () => { stopMedia(); showMedia('synthetic'); });
$('#recording').addEventListener('change', event => {
  const file = event.target.files[0];
  if (!file) return;
  stopMedia();
  recordingUrl = URL.createObjectURL(file);
  $('#recording-video').src = recordingUrl;
  showMedia('recording');
  event.target.value = '';
});
$('#recording-video').addEventListener('error', () => { toast('This recording could not be played. Try a browser-compatible MP4 or WebM.'); stopMedia(); showMedia('synthetic'); });
$('#export').addEventListener('click', () => {
  if (!state) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({ exported_at: new Date().toISOString(), ...state }, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = `pathfinder-session-${new Date().toISOString().replaceAll(':','-')}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
});
window.addEventListener('pagehide', stopMedia);
updateButtons();
poll();
requestAnimationFrame(drawScene);
