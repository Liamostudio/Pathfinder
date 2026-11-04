// Read-only presentation state. No control endpoints or saved manifest writes.
export const ARM_MAX_AGE_S=1.5;
export function freshArm(data,now=Date.now()/1000){
 return data?.state==='LIVE / READ-ONLY'&&Number.isFinite(data.updated)&&now-data.updated>=-.1&&now-data.updated<=ARM_MAX_AGE_S;
}
export function validJoints(joints){
 return joints&&typeof joints==='object'&&!Array.isArray(joints)&&Object.keys(joints).length>0&&Object.values(joints).every(v=>Number.isFinite(v)&&Math.abs(v)<=2*Math.PI);
}
export class JointEase{
 constructor(from,to,time,duration=220){this.from={...from};this.to={...to};this.time=time;this.duration=duration;}
 value(now,reduced=false){const t=reduced?1:Math.max(0,Math.min(1,(now-this.time)/this.duration)),ease=t*t*(3-2*t);return Object.fromEntries(Object.entries(this.to).map(([key,v])=>[key,(this.from[key]??v)+(v-(this.from[key]??v))*ease]));}
}
export class LiveArmView{
 constructor(onStatus=()=>{},clock=()=>Date.now()/1000){this.onStatus=onStatus;this.clock=clock;this.generation=0;this.enabled=false;this.status='ARM NOT LIVE';}
 setContext(renderer,manifest,enabled=false){this.generation++;this.renderer=renderer;this.manifest=manifest;this.enabled=enabled;this.data=null;this.appliedAt=null;this.failed=false;this.setStatus('ARM NOT LIVE');}
 setEnabled(enabled){this.generation++;this.enabled=enabled;this.appliedAt=null;if(this.manifest)this.renderer.update(this.manifest);this.setStatus('ARM NOT LIVE');}
 setStatus(text){if(text===this.status)return;this.status=text;this.onStatus(text);}
 refresh(){
  if(this.enabled&&this.data?.state==='UNAVAILABLE'&&this.appliedAt!==null){this.renderer.holdJointPose?.();this.setStatus('ARM DATA STALE');return;}
  if(!this.enabled||!this.manifest||this.data?.state!=='LIVE / READ-ONLY'){this.setStatus('ARM NOT LIVE');return;}
  if(!freshArm(this.data,this.clock())||this.failed||this.appliedAt===null||this.clock()-this.appliedAt>ARM_MAX_AGE_S){this.renderer.holdJointPose?.();this.setStatus('ARM DATA STALE');return;}
  this.setStatus('ARM LIVE · READ-ONLY');
 }
 async receive(data,getJoints){
  this.data=data;
  if(!this.enabled||!this.manifest)return this.refresh();
  if(data.state==='UNAVAILABLE'){this.generation++;return this.refresh();}
  if(data.state!=='LIVE / READ-ONLY'){
   this.generation++;this.appliedAt=null;this.renderer.update(this.manifest);return this.refresh();
  }
  if(!freshArm(data,this.clock())||this.pending)return this.refresh();
  if(this.appliedAt===data.updated&&!this.failed)return this.refresh();
  const generation=this.generation,manifest=this.manifest;this.pending=true;
  try{
   const reply=await getJoints();
   if(generation!==this.generation||manifest!==this.manifest||!freshArm(data,this.clock())||this.data?.state!=='LIVE / READ-ONLY')return;
   if(!validJoints(reply.joint_positions))throw Error('Invalid read-only joint positions');
   const view=structuredClone(manifest);view.robot.joint_positions={...view.robot.joint_positions,...reply.joint_positions};
   this.renderer.update(view,{easeJoints:true});this.appliedAt=data.updated;this.failed=false;
  }catch{if(generation===this.generation)this.failed=true;}
  finally{this.pending=false;this.refresh();}
 }
}
