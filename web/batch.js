'use strict';
let batchState={nodes:{}},batchSaving=Promise.resolve(),batchSaveTimer=null;
function batchReady(){
 planReady=selected.size>0&&config.nodes.filter(n=>selected.has(n.id)).every(n=>n.targetVersion&&batchState.nodes[n.id]?.inventory&&!batchState.nodes[n.id].inventory.error&&batchState.nodes[n.id].inventory.mode!=='blocked');
 $('execute').disabled=running||!planReady;
}
function renderBatchRows(){
 const focused=document.activeElement?.classList?.contains('batch-target')?{id:document.activeElement.dataset.node,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd}:null;
 $('batchRows').innerHTML=config.nodes.map((n,i)=>`<tr data-node="${esc(n.id)}"><td><input class="batch-pick" type="checkbox" value="${esc(n.id)}" aria-label="选择 ${esc(n.id)}" ${selected.has(n.id)?'checked':''}></td><td><strong>${esc(n.id)}</strong><small class="artifact-path">${esc(n.host)} · ${n.kind==='web'?'Web':'评测机'} · ${n.role==='standby'?'备用':'启用'}</small></td><td class="batch-current">未同步</td><td class="batch-available">未同步</td><td><input class="batch-target" aria-label="${esc(n.id)} 目标版本" data-node="${esc(n.id)}" list="batch-versions-${i}" placeholder="同步后选择，或手动填写" value="${esc(n.targetVersion||'')}"><datalist id="batch-versions-${i}"></datalist></td><td class="batch-mode">待检测</td></tr>`).join('')||'<tr><td colspan="6">请先在节点配置页添加服务器。</td></tr>';
 renderBatchState(batchState);
 if(focused){const field=[...$('batchRows').querySelectorAll('.batch-target')].find(el=>el.dataset.node===focused.id);if(field){field.focus();field.setSelectionRange(focused.start,focused.end);}}
}
function renderBatchState(s){
 batchState=s;
 for(const row of $('batchRows').querySelectorAll('[data-node]')){
  if(!row.matches('tr'))continue;
  const inv=s.nodes[row.dataset.node]?.inventory;
  row.querySelector('.batch-current').textContent=inv?.currentVersions?.join('、')||(inv?.error?'同步失败':inv?'无运行版本':'未同步');
  row.querySelector('.batch-available').textContent=inv?.availableVersions?.join('、')||(inv?.error?'未知':inv?'无':'未同步');
  row.querySelector('.batch-mode').textContent=inv?.reason||'待检测';
  row.querySelector('datalist').innerHTML=(inv?.availableVersions||[]).map(v=>`<option value="${esc(v)}"></option>`).join('');
  for(const input of row.querySelectorAll('input'))input.disabled=running;
 }
 if(s.action==='syncVersions')$('batchNotice').textContent='版本同步：'+s.status+'。同步只检查，不更新服务器。';
 if(s.action==='batchUpdate')$('batchNotice').textContent='批量更新：'+s.status+'。逐台执行，详情见下方日志。';
 batchReady();
}
$('batchRows').addEventListener('change',e=>{
 const input=e.target;
 if(input.classList.contains('batch-pick')){input.checked?selected.add(input.value):selected.delete(input.value);$('confirmPlan').checked=false;batchReady();}
 if(input.classList.contains('batch-target')){readBatchTarget(input);saveBatchTargets();}
});
function readBatchTarget(input){const n=config.nodes.find(n=>n.id===input.dataset.node);n.targetVersion=input.value.trim();$('confirmPlan').checked=false;batchReady();}
function saveBatchTargets(){
 clearTimeout(batchSaveTimer);batchSaveTimer=null;
 batchSaving=batchSaving.catch(()=>{}).then(async()=>{await save();batchReady();$('batchNotice').textContent='目标版本已保存到本机，尚未更新服务器。';}).catch(e=>{$('batchNotice').textContent='目标版本未保存：'+e.message;throw e;});
 batchSaving.catch(()=>{});return batchSaving;
}
$('batchRows').addEventListener('input',e=>{if(e.target.classList.contains('batch-target')){readBatchTarget(e.target);clearTimeout(batchSaveTimer);batchSaveTimer=setTimeout(saveBatchTargets,700);}});
window.addEventListener('beforeunload',e=>{if(batchSaveTimer){e.preventDefault();e.returnValue='目标版本尚在保存';}});
$('review').onclick=()=>tab('plan');
$('refreshPlan').onclick=()=>guard(async()=>{
 if(batchSaveTimer)saveBatchTargets();await batchSaving;await save();
 await api('run',{action:'syncVersions',config,selected:selected.size?[...selected]:undefined});await poll();
});
$('execute').onclick=()=>guard(async()=>{
 if(batchSaveTimer)saveBatchTargets();await batchSaving;batchReady();
 if(!planReady)throw Error('请先同步服务器、勾选节点，并为每台填写目标版本；同步失败或配置异常的节点不能执行。');
 if(!$('confirmPlan').checked)throw Error('请先核对目标版本并确认维护操作');
 await save();await api('run',{action:'batchUpdate',config,selected:[...selected]});await poll();
});
document.addEventListener('console-ready',renderBatchRows);
for(const [id,kind] of [['batchPickJudge','judge'],['batchPickWeb','web'],['batchPickNone',null]]){
 $(id).onclick=()=>{if(running)return;if(kind)config.nodes.filter(n=>n.kind===kind).forEach(n=>selected.add(n.id));else selected.clear();$('confirmPlan').checked=false;renderBatchRows();};
}
