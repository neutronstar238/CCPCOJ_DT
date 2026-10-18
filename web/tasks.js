'use strict';
// One task lock across pages; release only after the terminal result is applied.
const taskActivity={remote:false,ssh:false,artifacts:false,local:false};
const taskApplying=new Set(),taskStarting=new Set(),taskApplied={};
const taskOwners={'state':'remote','ssh/state':'ssh','artifacts/state':'artifacts','local/state':'local'};
const taskStarts={'run':'remote','ssh/run':'ssh','artifacts/run':'artifacts','local/run':'local'};
let taskEpoch=0;
function updateTaskControls(){
 running=Object.values(taskActivity).some(Boolean)||taskApplying.size>0||taskStarting.size>0;
 if(typeof renderRemoteControls==='function')renderRemoteControls();
 if(typeof renderSSHControls==='function')renderSSHControls();
 if(typeof renderArtifactControls==='function')renderArtifactControls();
 document.querySelectorAll('main button,main input,main select,dialog button,dialog input,dialog select').forEach(el=>{
  if(['stop','localStopTask','cancelNode','sshCancelImport','cancelImport','archiveDeleteCancel'].includes(el.id))return;
  // Keep each control's own validation/selection-based disabled state intact.
  if(running){el.inert=true;el.setAttribute('data-task-lock','');el.setAttribute('aria-disabled','true');}
  else if(el.hasAttribute('data-task-lock')){el.inert=false;el.removeAttribute('inert');el.removeAttribute('data-task-lock');el.removeAttribute('aria-disabled');}
 });
}
const taskAPI=api;
api=async function(path,data,raw=false){
 const owner=taskStarts[path];
 if(owner){
  updateTaskControls();
  if(running)throw Error('其他任务或结果刷新尚未完成，请稍候');
  taskEpoch++;taskStarting.add(owner);updateTaskControls();
 }
 const epoch=taskEpoch;
 try{
  let result=await taskAPI(path,data,raw);
  // A state read started before a new task must not unlock its controls.
  if(taskOwners[path]&&epoch!==taskEpoch)result=await taskAPI(path,data,raw);
  if(owner)taskActivity[owner]=true;
  const stateOwner=taskOwners[path];
  if(stateOwner){
   if(result.activity)Object.assign(taskActivity,result.activity);
   taskActivity[stateOwner]=!!result.running;
   if(result.job&&!result.running&&taskApplied[stateOwner]!==result.job)taskApplying.add(stateOwner);
  }
  return result;
 }finally{if(owner)taskStarting.delete(owner);updateTaskControls();}
};
function serialTaskPoll(owner,read){
 let pending=null;
 return function(){
  if(pending)return pending;
  pending=(async()=>{
   try{
    await read();
    // The module marks seen only once all dependent reads have succeeded.
    const seen={remote:remoteSeenJob,ssh:sshSeenJob,artifacts:artifactSeen,local:localSeenJob}[owner];
    if(seen)taskApplied[owner]=seen;
    taskApplying.delete(owner);
   }finally{pending=null;updateTaskControls();}
  })();
  return pending;
 };
}
poll=serialTaskPoll('remote',poll);
pollSSH=serialTaskPoll('ssh',pollSSH);
artifactPoll=serialTaskPoll('artifacts',artifactPoll);
pollLocal=serialTaskPoll('local',pollLocal);
// Newly rendered rows inherit the same lock without waiting for the next poll.
const taskObserver=new MutationObserver(updateTaskControls);
taskObserver.observe(document.body,{childList:true,subtree:true});
