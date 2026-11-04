// Manual restrictions share the revision-checked edit API; observations are untouched.
export async function clearManualBlocks(root,api,current=()=>true){
 if(!current())throw Error('Working area changed; try reset again');
 const snapshot=await api(root+'/voxels'),indices=snapshot.manual_blocked||[];
 if(!current())throw Error('Working area changed; try reset again');
 if(!indices.length)return {snapshot,cleared:0};
 const result=await api(root+'/voxel-edits',{revision:snapshot.revision,policy_revision:snapshot.policy_revision,indices,blocked:false});
 return {snapshot:result,cleared:indices.length};
}
