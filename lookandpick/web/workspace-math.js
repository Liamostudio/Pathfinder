// Row-major affine matrices and OpenCV camera conventions throughout.
export const I=()=>[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
export const mul=(a,b)=>a.map(row=>b[0].map((_,j)=>row.reduce((s,x,k)=>s+x*b[k][j],0)));
export const point=(m,p)=>m.slice(0,3).map(row=>row[3]+p.reduce((s,x,i)=>s+x*row[i],0));
export const direction=(m,p)=>m.slice(0,3).map(row=>p.reduce((s,x,i)=>s+x*row[i],0));
export const dot=(a,b)=>a.reduce((s,x,i)=>s+x*b[i],0);
export const transpose=a=>a[0].map((_,i)=>a.map(r=>r[i]));
export const xyz=(translation=[0,0,0],angles=[0,0,0],scale=1)=>{
 const [x,y,z]=angles,[cx,cy,cz]=angles.map(Math.cos),[sx,sy,sz]=angles.map(Math.sin);
 const m=[[cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx,translation[0]],[sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx,translation[1]],[-sy,cy*sx,cy*cx,translation[2]],[0,0,0,1]];
 for(let i=0;i<3;i++)for(let j=0;j<3;j++)m[i][j]*=scale;return m;
};
export function axisRotation(axis,angle){const length=Math.hypot(...axis),[x,y,z]=axis.map(v=>v/length),c=Math.cos(angle),s=Math.sin(angle),t=1-c;return [[t*x*x+c,t*x*y-s*z,t*x*z+s*y,0],[t*x*y+s*z,t*y*y+c,t*y*z-s*x,0],[t*x*z-s*y,t*y*z+s*x,t*z*z+c,0],[0,0,0,1]];}
export function quaternion(m){
 const trace=m[0][0]+m[1][1]+m[2][2];let q;
 if(trace>0){const s=Math.sqrt(trace+1)*2;q=[s/4,(m[2][1]-m[1][2])/s,(m[0][2]-m[2][0])/s,(m[1][0]-m[0][1])/s];}
 else{let i=0;if(m[1][1]>m[i][i])i=1;if(m[2][2]>m[i][i])i=2;const j=(i+1)%3,k=(i+2)%3,s=Math.sqrt(1+m[i][i]-m[j][j]-m[k][k])*2;q=[0,0,0,0];q[0]=(m[k][j]-m[j][k])/s;q[i+1]=s/4;q[j+1]=(m[j][i]+m[i][j])/s;q[k+1]=(m[k][i]+m[i][k])/s;}
 return q;
}
export const qmul=(a,b)=>[a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3],a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2],a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1],a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]];
export function gaussianFrame(manifest){const t=manifest.frames.workspace_from_map,n=manifest.frames.gaussian_normalization,g=I();for(let i=0;i<3;i++){g[i][i]=n.radius;g[i][3]=n.centre[i];}return mul(t,g);}
export function cameraInFrame(camera,frame){
 const scale=Math.hypot(...frame.slice(0,3).map(row=>row[0]));const rot=frame.slice(0,3).map(row=>row.slice(0,3).map(x=>x/scale)),old=camera.world_to_camera,r=old.slice(0,3).map(row=>row.slice(0,3));
 const centre=transpose(r).map(row=>-dot(row,old.slice(0,3).map(row=>row[3]))),next=point(frame,centre);
 // row * rot^T: each output entry is the dot product with a row of rot.
 const rotation=r.map(row=>rot.map(axis=>dot(row,axis)));
 return {...camera,world_to_camera:rotation.map(row=>[...row,-dot(row,next)]).concat([[0,0,0,1]])};
}
export function transformGaussians(viewer,manifest){
 const frame=gaussianFrame(manifest),scale=Math.hypot(frame[0][0],frame[1][0],frame[2][0]),rotation=frame.slice(0,3).map(row=>row.slice(0,3).map(x=>x/scale)),q=quaternion(rotation),data=viewer.data;
 for(let i=0;i<data.length;i+=14){data.set(point(frame,Array.from(data.slice(i,i+3))),i);for(let j=3;j<6;j++)data[i+j]*=scale;data.set(qmul(q,Array.from(data.slice(i+6,i+10))),i+6);}
 viewer.metadata.cameras=viewer.metadata.cameras.map(camera=>cameraInFrame(camera,frame));
 viewer.target=point(frame,viewer.target);viewer.up=[0,0,1];viewer.dirty=true;return frame;
}
export function rectangle(surface,scale=1){const c=[...surface.centre];c[2]=surface.height;const yaw=surface.yaw*Math.PI/180,u=[Math.cos(yaw),Math.sin(yaw),0],v=surface.name==='table'?[-Math.sin(yaw),Math.cos(yaw),0]:[0,0,1];return [[-1,-1],[1,-1],[1,1],[-1,1]].map(([a,b])=>c.map((x,i)=>(x+a*u[i]*surface.width/2+b*v[i]*surface.depth/2)*scale));}
export function resizeRectangle(surface,corner,position){
 const corners=rectangle(surface),fixed=corners[(corner+2)%4],yaw=surface.yaw*Math.PI/180,u=[Math.cos(yaw),Math.sin(yaw),0],v=[-Math.sin(yaw),Math.cos(yaw),0],delta=position.map((x,i)=>x-fixed[i]);
 const a=dot(delta,u),b=dot(delta,v),out=structuredClone(surface);out.width=Math.max(.01,Math.abs(a));out.depth=Math.max(.01,Math.abs(b));out.centre=fixed.map((x,i)=>x+(a*u[i]+b*v[i])/2);out.centre[2]=out.height;return out;
}
