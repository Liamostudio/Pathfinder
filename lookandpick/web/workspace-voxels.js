// Pure geometry shared by the working-area editor, renderer and UI tests.
export const EDGE_PAIRS=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
export function gridShape(a){return ['width','depth','height'].map(k=>Math.ceil(a[k]/a.cell_size-1e-6));}
export function cellCorners(a,index){
 const [nx,ny]=gridShape(a),ix=index%nx,iy=Math.floor(index/nx)%ny,iz=Math.floor(index/(nx*ny)),angle=a.yaw*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
 return [[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]].map(([x,y,z])=>{x=Math.min(a.width,(ix+x)*a.cell_size)-a.width/2;y=Math.min(a.depth,(iy+y)*a.cell_size)-a.depth/2;return [a.centre[0]+c*x-s*y,a.centre[1]+s*x+c*y,a.base+Math.min(a.height,(iz+z)*a.cell_size)];});
}
export function volumeCorners(a){return cellCorners({...a,cell_size:1,width:1,depth:1,height:1,yaw:0,centre:[0,0],base:0},0).map(p=>{const x=(p[0]+.5)*a.width-a.width/2,y=(p[1]+.5)*a.depth-a.depth/2,t=a.yaw*Math.PI/180;return [a.centre[0]+Math.cos(t)*x-Math.sin(t)*y,a.centre[1]+Math.sin(t)*x+Math.cos(t)*y,a.base+p[2]*a.height];});}
export function footprintFromDrag(start,end,table,cell=.05,height=.3){
 const angle=table.yaw*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle),local=p=>{const x=p[0]-table.centre[0],y=p[1]-table.centre[1];return [Math.max(-table.width/2,Math.min(table.width/2,c*x+s*y)),Math.max(-table.depth/2,Math.min(table.depth/2,-s*x+c*y))];},a=local(start),b=local(end),x=(a[0]+b[0])/2,y=(a[1]+b[1])/2;
 return {enabled:true,centre:[table.centre[0]+c*x-s*y,table.centre[1]+s*x+c*y],width:Math.max(cell,Math.abs(a[0]-b[0])),depth:Math.max(cell,Math.abs(a[1]-b[1])),base:table.height,height:Math.max(cell,height),yaw:table.yaw,cell_size:cell,ttl_ms:500,hold_ms:750,margin:cell*.15};
}
export function voxelLines(area,snapshot,scale=1){
 const shape=gridShape(area),count=shape.reduce((a,b)=>a*b,1);if(count>4096)throw Error('Increase cube size to stay within 4,096 cells');const values=[],palette={free:[.28,.85,.42],occupied:[1,.23,.16],unknown:[.85,.6,.18]};
 for(let i=0;i<count;i++){const corners=cellCorners(area,i),colour=palette[snapshot?.states?.[i]||'unknown'];for(const edge of EDGE_PAIRS)for(const j of edge)values.push(...corners[j].map(v=>v*scale),...colour,0,0,1);}
 return new Float32Array(values);
}
export function heightFromDrag(initial,delta,screenAxis,cell,max=10){const length=screenAxis[0]**2+screenAxis[1]**2;if(length<4)return initial;const value=initial+(delta[0]*screenAxis[0]+delta[1]*screenAxis[1])/length;return Math.max(cell,Math.min(max,Math.round(value/cell)*cell));}

export function remainingFreshness(snapshot,ttl,now=Date.now()){return Math.max(0,Math.min(ttl,snapshot.valid_for_ms??0)-Math.max(0,now-(snapshot.generated_at_ms??0)));}

export function cellCoordinates(area,index){const [nx,ny]=gridShape(area);return [index%nx,Math.floor(index/nx)%ny,Math.floor(index/(nx*ny))];}
export function cellCentre(area,index){const corners=cellCorners(area,index);return corners[0].map((v,i)=>(v+corners[6][i])/2);}
export function rowIndices(area,index,axis){const shape=gridShape(area),c=cellCoordinates(area,index);return Array.from({length:shape[axis]},(_,i)=>{const p=[...c];p[axis]=i;return (p[2]*shape[1]+p[1])*shape[0]+p[0];});}
export function edgeAxis(corner,next){const v=[[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]];return v[corner].findIndex((x,i)=>x!==v[next][i]);}
export function scaledCorners(area,index,size){const centre=cellCentre(area,index);return cellCorners(area,index).map(p=>p.map((v,i)=>centre[i]+(v-centre[i])*size));}
export function rayBox(origin,direction,low,high){let near=0,far=Infinity;for(let i=0;i<3;i++){if(Math.abs(direction[i])<1e-10){if(origin[i]<low[i]||origin[i]>high[i])return null;}else{const a=(low[i]-origin[i])/direction[i],b=(high[i]-origin[i])/direction[i];near=Math.max(near,Math.min(a,b));far=Math.min(far,Math.max(a,b));if(near>far)return null;}}return far>=0?near:null;}
export function pickVoxel(area,origin,direction,sizes=null,preferred=null){
 const t=area.yaw*Math.PI/180,c=Math.cos(t),s=Math.sin(t),dx=origin[0]-area.centre[0],dy=origin[1]-area.centre[1],o=[c*dx+s*dy,-s*dx+c*dy,origin[2]-area.base],d=[c*direction[0]+s*direction[1],-s*direction[0]+c*direction[1],direction[2]],shape=gridShape(area),count=shape.reduce((a,b)=>a*b,1);let best=null;
 const hit=index=>{const dimensions=[area.width,area.depth,area.height],coords=cellCoordinates(area,index),low=coords.map(v=>v*area.cell_size),high=coords.map((v,i)=>Math.min(dimensions[i],(v+1)*area.cell_size)),p=low.map((v,i)=>(v+high[i])/2-(i<2?dimensions[i]/2:0)),half=high.map((v,i)=>(v-low[i])*(sizes?.[index]??.5)/2);return rayBox(o,d,p.map((v,i)=>v-half[i]),p.map((v,i)=>v+half[i]));};
 if(preferred!==null&&preferred>=0&&preferred<count){const distance=hit(preferred);if(distance!==null)return {index:preferred,distance};}
 for(let i=0;i<count;i++){const distance=hit(i);if(distance!==null&&(!best||distance<best.distance))best={index:i,distance};}return best;
}
export function springStep(value,velocity,target,dt,reduced=false){if(reduced)return [target,0];dt=Math.min(.034,Math.max(0,dt));velocity+=(200*(target-value)-12*velocity)*dt;value+=velocity*dt;return [Math.max(.30,Math.min(1,value)),velocity];}
export function fidelityCellSize(area,budget,units=1){
 const count=size=>gridShape({...area,cell_size:size}).reduce((a,b)=>a*b,1);let low=.005,high=1;
 if(count(high)>budget)return high;
 for(let i=0;i<45;i++){const middle=(low+high)/2;if(count(middle)>budget)low=middle;else high=middle;}
 const step=Math.max(.00001,.001*units);return Math.min(1,Math.max(.005,Math.ceil((high-1e-9)/step)*step));
}
