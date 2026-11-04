// Display classes use sensor evidence separately from durable manual policy.
export const allVoxelClasses=()=>({allowed:true,unknown:true,manual:true});
export const filterActive=(filter,mode)=>mode==='monitor'&&['allowed','unknown','manual'].some(k=>filter?.[k]===false);
export function voxelStyle(observed,blocked,mode,filter){
 const live=observed==='occupied',unsafe=live||blocked;
 const visible=mode==='edit'||live||(blocked?filter?.manual!==false:observed==='free'?filter?.allowed!==false:filter?.unknown!==false);
 return {visible,unsafe,live,colour:live?[1,.34,.12]:blocked?[.82,.16,.26]:mode==='edit'?[.48,.90,.66]:observed==='free'?[.38,.87,.52]:[.76,.66,.42]};
}
export const filterSuffix=(filter,mode)=>filterActive(filter,mode)?' · display filter: intrusions only':'';
export function voxelStatus(snap,filter,mode){
 if(!snap?.enabled)return 'Draw a working area to start.';
 const c=snap.counts;
 return `${snap.simulation?'SIMULATED PEOPLE/HANDS · ':snap.has_recorded_evidence?'RECORDED RGB PEOPLE/HANDS · ':''}${c.free} clear · ${c.occupied} unsafe · ${c.unknown} unknown${snap.fresh?'':' · no fresh observations'}${filterSuffix(filter,mode)}`;
}
export function mountVoxelFilter(container,getCubes,onChange=()=>{}){
 let filter=allVoxelClasses();
 container.innerHTML='<legend>Show · Live status only</legend><label><input type="checkbox" data-class="allowed" checked> Allowed (green)</label><label><input type="checkbox" data-class="unknown" checked> Unknown (tan)</label><label><input type="checkbox" data-class="manual" checked> Manual no-go (steady red)</label><label><input type="checkbox" checked disabled> Live intrusions (bright coral, always shown)</label><button type="button" class="text-key" data-preset="intrusions">Intrusions only</button><button type="button" class="text-key" data-preset="all">Show all</button><small>Unknown cells remain unsafe. Filtering changes the display only; Edit layout always shows all cubes.</small>';
 const apply=()=>{const cubes=getCubes();if(cubes)cubes.setDisplayFilter(filter);onChange();};
 for(const input of container.querySelectorAll('[data-class]'))input.onchange=()=>{filter[input.dataset.class]=input.checked;apply();};
 for(const button of container.querySelectorAll('[data-preset]'))button.onclick=()=>{filter=button.dataset.preset==='all'?allVoxelClasses():{allowed:false,unknown:false,manual:true};for(const input of container.querySelectorAll('[data-class]'))input.checked=filter[input.dataset.class];apply();};
 return apply;
}
