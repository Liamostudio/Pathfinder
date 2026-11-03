// Estimates are ranges, calibrated by completed local fits where available.
export const QUALITY_PRESETS={
 1000:{name:'Quick draft',steps:1000,train_edge:512,max_gaussians:25000},
 2500:{name:'Balanced',steps:2500,train_edge:640,max_gaussians:40000},
 6000:{name:'High detail',steps:6000,train_edge:960,max_gaussians:80000},
 20000:{name:'Ultra-high detail',steps:20000,train_edge:1280,max_gaussians:160000},
};
export function duration(seconds){
 if(!Number.isFinite(seconds))return 'unknown';
 if(seconds<60)return `${Math.max(5,Math.ceil(seconds/5)*5)} sec`;
 if(seconds<3600)return `${Math.ceil(seconds/60)} min`;
 const minutes=Math.ceil(seconds/60);return `${Math.floor(minutes/60)} hr${minutes%60?' '+minutes%60+' min':''}`;
}
function workScale(preset){return (preset.train_edge/640)**2*Math.sqrt(preset.max_gaussians/40000);}
export function estimateBuild(preset,frames,scenes=[]){
 const measured=scenes.filter(scene=>scene.trained&&scene.training?.history?.length).map(scene=>{
  const last=scene.training.history.at(-1);
  const reference={train_edge:scene.training.max_edge||640,max_gaussians:scene.training.max_gaussians||40000};
  return last.elapsed_s/last.step/workScale(reference);
 }).filter(rate=>Number.isFinite(rate)&&rate>0).sort((a,b)=>a-b);
 const perStep=measured.length?measured[Math.floor(measured.length/2)]:.008;
 const fitting=preset.steps*perStep*workScale(preset);
 const cameraSolve=12+frames*.8+(frames<=80?frames*frames*.025:frames*.4);
 const exportTime=5+frames*.08;
 const total=fitting+cameraSolve+exportTime;
 return {low:Math.max(15,total*.55),high:Math.max(30,total*1.85),calibrated:measured.length>0};
}
export function remainingTime(entry){
 if(entry.stage!=='training'||!entry.running||entry.step<100||!entry.steps||!entry.elapsed_s)return null;
 const seconds=entry.elapsed_s/entry.step*(entry.steps-entry.step)+5+(entry.reconstruction?.registered||0)*.08;
 return {low:Math.max(5,seconds*.75),high:Math.max(10,seconds*1.4)};
}
