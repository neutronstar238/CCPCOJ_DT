'use strict';
let localData=null,localBusy=false,localSelected=new Set(),localSaving=Promise.resolve(),localDirty=false,localSaveTimer=null,localRevision=0,localSeenJob=null;
function localSettings(){const s={};for(const el of $('localSettings').querySelectorAll('[data-local]'))if(!el.disabled)s[el.dataset.local]=el.type==='number'?Number(el.value):el.value;for(const el of $('localRows').querySelectorAll('[data-version]'))s[el.dataset.version+'Version']=el.value.trim();return s;}
function saveLocal(){
 clearTimeout(localSaveTimer);localSaveTimer=null;const revision=++localRevision;
 localDirty=true;$('localConfirm').checked=false;
 const settings=localSettings();
 localSaving=localSaving.catch(()=>{}).then(async()=>{const r=await api('local/save',{settings});localData.config=r.config;if(revision===localRevision){localDirty=false;$('localSaveStatus').textContent='已自动保存 · '+localData.configPath;}}).catch(e=>{$('localSaveStatus').textContent='未保存：'+e.message;throw e;});
 localSaving.catch(()=>{});return localSaving;
}
async function refreshLocal(){
 if(localSaveTimer)saveLocal();await localSaving;
 const [data,distros]=await Promise.all([api('local/config'),api('local/distros').catch(e=>({distros:[],error:e.message}))]);localData=data;
 const c=data.config,w=c.nodes.find(n=>n.kind==='web'),j=c.nodes.find(n=>n.kind==='judge');
 $('localDistro').innerHTML='<option value="">请选择已安装的 Ubuntu 24.04</option>'+distros.distros.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');
 const values={...c.local,port:w.port,myadminPort:w.myadminPort,adminUser:w.adminUser,pods:j.pods,judgeUser:j.judgeUser,judgePassword:j.judgePassword};
 for(const el of $('localSettings').querySelectorAll('[data-local]')){el.value=values[el.dataset.local]??'';const key=el.dataset.local;el.disabled=['distro','path'].includes(key)?!!w.prepared||!!j.prepared:['port','myadminPort','adminUser','adminPassword'].includes(key)?!!w.prepared:!!j.prepared;}
 $('localSaveStatus').textContent=distros.error?distros.error:'本地配置：'+data.configPath;
 $('localCredentials').textContent=w.prepared?`管理员：${w.adminUser}；初始密码：${w.secrets.admin}（以后在站点改密不会自动同步）`:'首次部署后显示管理员登录资料。';
 $('localOpen').hidden=!w.deployed;$('localOpen').href='http://'+w.host+':'+w.port;
 $('localTool').textContent='部署程序：'+(data.tool?.version||data.tool?.name||'尚未选择，请到“版本与镜像分发”导入或下载');
 $('localCache').innerHTML='<p class="hint">缓存目录：'+esc(data.cache.cacheDir)+'</p>'+data.cache.archives.filter(a=>a.available).map(a=>`<p>${Object.keys(a.images).map(esc).join('、')} · ${(a.size/1048576).toFixed(1)} MB</p>`).join('');
 $('localRows').innerHTML=c.nodes.map(n=>`<tr data-local-node="${n.id}"><td><input type="checkbox" class="local-pick" value="${n.id}" aria-label="选择本地 ${n.kind==='web'?'Web':'PCJ'}" ${localSelected.has(n.id)?'checked':''}></td><td><strong>${n.kind==='web'?'Web':'PCJ 评测机 · '+n.pods+' pods'}</strong><div class="local-status" role="status">未检测</div><small class="local-checked hint"></small></td><td class="local-versions" data-label="版本">未检测</td><td data-label="目标版本"><input data-version="${n.kind}" aria-label="本地 ${n.kind} 目标版本" value="${esc(n.targetVersion||'')}" list="local-list-${n.kind}" placeholder="选择缓存版本，或手动填写"><datalist id="local-list-${n.kind}"></datalist></td></tr>`).join('');
 renderLocalState(data.state);
}
function localServiceState(n,state){
 const containers=state.containers,expected=n.kind==='web'?4:n.pods;
 if(state.error)return {tone:'error',text:state.serviceStatus||'检测失败'};
 if(state.dockerRunning===false)return {tone:'stopped',text:'Docker 未运行'};
 if(!Array.isArray(containers))return {tone:'unknown',text:'未检测'};
 const running=containers.filter(c=>c.running).length;
 const pods=n.kind==='judge'?` · ${state.runningPods??running}/${n.pods} pods`:'';
 if(!n.deployed&&!containers.length)return {tone:'unknown',text:'尚未部署'};
 if(containers.length!==expected||(n.kind==='judge'&&(state.totalPods??containers.length)!==expected))return {tone:'error',text:'容器数量异常'+pods};
 if(!running)return {tone:'stopped',text:'已关闭'+pods};
 if(running<expected)return {tone:'error',text:'部分运行'+pods};
 return {tone:'running',text:'运行中'+pods};
}
function renderLocalState(s){
 localBusy=s.running;$('localJobState').textContent=s.status;$('localLogs').textContent=s.logs.join('\n')||'尚未执行本地操作。';
 if(!localData)return;
 for(const n of localData.config.nodes){
  const row=$('localRows').querySelector(`[data-local-node="${n.id}"]`);if(!row)continue;
  const state=s.nodes[n.id]||{},inv=state.inventory;
  const cache=localData.cache.archives.filter(a=>a.available).flatMap(a=>Object.keys(a.images)).filter(ref=>ref.startsWith('csgrandeur/ccpcoj-'+(n.kind==='web'?'web2':'judge2')+':')).map(ref=>ref.split(':').pop());
  row.querySelector('datalist').innerHTML=[...new Set([...cache,...(inv?.availableVersions||[])])].map(v=>`<option value="${esc(v)}"></option>`).join('');
  row.querySelector('.local-versions').textContent='容器版本：'+(inv?.currentVersions?.join('、')||n.deployedVersion||'未检测')+'\nWSL 已有：'+(inv?.availableVersions?.join('、')||'未检测');
  const status=localServiceState(n,state),badge=row.querySelector('.local-status');
  badge.textContent=status.text;badge.dataset.tone=status.tone;badge.title=state.error||state.serviceStatus||'';
  row.querySelector('.local-checked').textContent=state.checkedAt?'最近检测：'+state.checkedAt.replace('T',' '):'点击获取本地版本与状态';
  for(const el of row.querySelectorAll('input'))el.disabled=localBusy;
 }
 for(const id of ['localRefresh','localSync','localStart','localStop','localUpdate','localRecover'])$(id).disabled=localBusy;
 if(localBusy)for(const el of $('localSettings').querySelectorAll('input,select'))el.disabled=true;
}
async function localRun(action){
 if(localSaveTimer)saveLocal();await localSaving;
 if(localDirty)throw Error('本地设置尚未保存，请修正输入后重试');
 const ids=[...localSelected];if(!ids.length&&action!=='syncVersions')throw Error('请勾选本地 Web 或 PCJ');
 if(['batchUpdate','recoverUpdate'].includes(action)&&!$('localConfirm').checked)throw Error('请核对本地目标并勾选确认');
 await api('local/run',{action,selected:ids,confirmed:$('localConfirm').checked});$('localNotice').textContent='本地任务已开始，详细进度见下方日志。';await pollLocal();
}
async function pollLocal(){if(!localData)return;const was=localBusy,s=await api('local/state');renderLocalState(s);if(!s.running&&s.job&&s.job!==localSeenJob){localBusy=true;await refreshLocal();localSeenJob=s.job;localBusy=false;}}
$('localSettings').addEventListener('change',e=>{if(e.target.id==='localJudgeMode'&&e.target.value==='external')$('localJudgePassword').value='';saveLocal();});
function scheduleLocalSave(){localDirty=true;localRevision++;$('localConfirm').checked=false;clearTimeout(localSaveTimer);$('localSaveStatus').textContent='正在自动保存…';localSaveTimer=setTimeout(saveLocal,500);}
$('localSettings').addEventListener('input',scheduleLocalSave);
$('localRows').addEventListener('input',e=>{if(e.target.dataset.version)scheduleLocalSave();});
$('localRows').addEventListener('change',e=>{if(e.target.classList.contains('local-pick')){e.target.checked?localSelected.add(e.target.value):localSelected.delete(e.target.value);$('localConfirm').checked=false;}else if(e.target.dataset.version)saveLocal();});
$('localRefresh').onclick=()=>guard(refreshLocal);
$('localStopTask').onclick=()=>guard(async()=>{await api('local/cancel',{});$('localNotice').textContent='已请求在安全边界停止；当前数据保护步骤完成后结束。';});
for(const [id,action] of [['localSync','syncVersions'],['localStart','start'],['localStop','stop'],['localUpdate','batchUpdate'],['localRecover','recoverUpdate']])$(id).onclick=()=>guard(()=>localRun(action));
document.addEventListener('console-ready',()=>setInterval(()=>{if(localData)pollLocal().catch(e=>{$('localNotice').textContent=e.message;});},2000));
window.addEventListener('beforeunload',e=>{if(localBusy||localDirty){e.preventDefault();e.returnValue='本地任务或设置尚未完成';}});
