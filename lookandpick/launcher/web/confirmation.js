// In-page confirmation: never uses a native browser prompt or retained returnValue.
export function confirmation(root,message,cancel,accept,background=[]){
 let pending=null,previous=null,inert=[];
 const finish=ok=>{if(!pending)return;root.hidden=true;inert.forEach(([node,value])=>node.inert=value);const resolve=pending;pending=null;previous?.focus?.();resolve(ok);};
 cancel.onclick=()=>finish(false);accept.onclick=()=>finish(true);
 root.onclick=event=>{if(event.target===root)finish(false);};
 root.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();finish(false);}else if(event.key==='Tab'){event.preventDefault();const active=root.ownerDocument.activeElement;(active===cancel?accept:cancel).focus();}};
 return text=>{if(pending)return Promise.resolve(false);message.textContent=text;previous=root.ownerDocument.activeElement;inert=background.map(node=>[node,node.inert]);inert.forEach(([node])=>node.inert=true);root.hidden=false;return new Promise(resolve=>{pending=resolve;cancel.focus();});};
}
