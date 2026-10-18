let databaseLastState=null,databasePendingAction=null;
function renderDatabaseState(state){
 databaseLastState=state;
 const node=config.nodes.find(n=>n.id===$('targetNode').value),valid=node?.kind==='web';
 const data=state.nodes[node?.id]||{},storage=data.storage,plan=data.databasePlan;
 for(const id of ['databaseInspect','databaseCheck','databaseRecover'])$(id).disabled=running||!valid;
 $('databaseMigrate').disabled=running||!valid||!plan||plan.target!==$('databaseTarget').value.trim();
 $('databaseLocations').innerHTML=!valid?'<p>请选择 Web 节点。</p>':!storage?'<p>尚未检测实际目录。</p>':
  `<div class="tablewrap"><table><thead><tr><th>内容</th><th>服务器目录</th><th>实际设备 / 挂载点</th><th>可用空间</th></tr></thead><tbody>${storage.locations.map(x=>`<tr><td>${esc(x.label)}</td><td>${esc(x.path)}${x.exists?'':'（尚未建立）'}</td><td>${esc(x.device)}<br>${esc(x.mount)} · ${esc(x.filesystem)}</td><td>${(x.freeBytes/1024**3).toFixed(1)} GiB</td></tr>`).join('')}</tbody></table></div>${storage.pending?'<p class="error">存在未完成迁移，请先恢复。</p>':''}${storage.managedStorage?`<p>工具配置的数据库实际目录：${esc(storage.managedStorage.target)}</p>`:''}`;
 $('databasePlan').textContent=plan?`已检查：${plan.source} → ${plan.target}；数据 ${(plan.bytes/1024**3).toFixed(2)} GiB，目标可用 ${(plan.freeBytes/1024**3).toFixed(1)} GiB。${plan.sameDisk?'目标仍在同一设备上。':''} ${plan.target!==$('databaseTarget').value.trim()?'目标已修改，请重新检查。':'尚未迁移。'}`:'尚未检查迁移目标。迁移会暂停 Web 及关联评测机。';
}
for(const action of ['databaseInspect','databaseCheck'])$(action).onclick=()=>guard(()=>start(action,[$('targetNode').value],{target:$('databaseTarget').value.trim()}));
$('databaseTarget').addEventListener('input',()=>{if(databaseLastState)renderDatabaseState(databaseLastState);});
for(const action of ['databaseMigrate','databaseRecover'])$(action).onclick=()=>guard(async()=>{
 if(!$('confirmSwitch').checked)throw Error('请先勾选下方维护时间确认');
 const id=$('targetNode').value,node=config.nodes.find(n=>n.id===id);
 if(node?.kind!=='web')throw Error('请选择 Web 节点');
 const target=$('databaseTarget').value.trim();
 databasePendingAction={action,id,target};
 $('databaseDialogTitle').textContent=action==='databaseMigrate'?'确认迁移数据库？':'确认恢复未完成迁移？';
 $('databaseConfirmDetails').textContent=`节点：${id} · ${node.host}\n`+(action==='databaseMigrate'?`目标目录：${target}\n仅迁移 MySQL 数据；不修改 SQL 口令和管理员账户。`:'将恢复未提交事务的原数据库挂载，再启动原服务。已提交的迁移不会回退。');
 $('databaseDialog').showModal();$('databaseCancel').focus();
});
$('databaseCancel').onclick=()=>{$('databaseDialog').close();databasePendingAction=null;};
$('databaseConfirm').onclick=()=>guard(async()=>{
 if(!databasePendingAction)return;
 const p=databasePendingAction;databasePendingAction=null;$('databaseDialog').close();
 await start(p.action,[p.id],{target:p.target,confirmed:true});
});
