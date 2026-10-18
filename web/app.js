'use strict';
let token=location.hash.slice(1)||sessionStorage.getItem('csg-session')||'';
if(token)sessionStorage.setItem('csg-session',token);history.replaceState(null,'',location.pathname);
let nodeSaveBusy=false,planReady=false,remoteSeenJob=null;
let config={nodes:[]},running=false,editing=null,editIndex=-1,importCandidate=null,selected=new Set();
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notify(s){const box=$('nodeDialog').open?$('nodeError'):$('notice');box.hidden=false;box.textContent=s;if($('nodeDialog').open)box.scrollIntoView({block:'nearest'});}
async function api(path,data,raw=false){const options={headers:{'X-CSG-Token':token}};if(data!==undefined){if(raw&&data instanceof File)options.headers['X-CSG-Filename']=encodeURIComponent(data.name);options.method='POST';options.body=raw?data:JSON.stringify(data);options.headers['Content-Type']=raw?'application/octet-stream':'application/json';}const r=await fetch('/api/'+path,options);const j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j;}
async function guard(fn){try{await fn();}catch(e){notify(e.message);}}
const labels={localDeploy:['本地部署','在 WSL 中运行本地 Web 和评测机，复用本地镜像缓存。'],sshModule:['SSH 密钥管理','管理本地私钥与服务器连接，独立配置免密 SSH。'],configure:['节点配置','添加节点时可复用 SSH 连接；字段填写完整后自动保存到本机。'],plan:['批量更新','同步服务器镜像，逐台指定目标版本；自动判断更新或首次初始化。'],monitor:['状态与主备切换','检测服务器、备份数据、切换备用节点和维护主机。'],recovery:['部署资料','下载部署信息、业务口令和导入模板。'],guide:['使用说明','各项功能可以独立使用，不需要重复按顺序操作。']};
function tab(name){if(name==='localDeploy'&&typeof refreshLocal==='function')guard(refreshLocal);$('taskLogPanel').hidden=!['artifacts','plan','monitor'].includes(name);if(name==='artifacts'&&typeof refreshArtifactCache==='function')guard(refreshArtifactCache);if(name==='recovery')guard(refreshCredentialNodes);document.querySelectorAll('.tab').forEach(e=>e.hidden=e.id!==name);document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.tab===name));$('pageTitle').textContent=labels[name][0];$('pageSubtitle').textContent=labels[name][1];window.scrollTo(0,0);}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>tab(b.dataset.tab));
function field(n,key,title,type='text',extra=''){const v=key==='adminPassword'?n.secrets?.admin:n[key];return `<label>${title}<input data-key="${key}" type="${type}" value="${esc(v)}" ${extra}></label>`;}
function select(n,key,title,values){return `<label>${title}<select data-key="${key}">${values.map(([v,t])=>`<option value="${esc(v)}" ${n[key]===v?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;}
function editNode(index,kind){if(nodeSaveBusy){notify('节点仍在保存，请稍候再打开编辑器');return;}nodeFinishRequested=false;$('nodeError').hidden=true;$('nodeError').textContent='';$('nodeSaveState').textContent='字段填写完整后自动保存到本机';editIndex=index;editing=index>=0?structuredClone(config.nodes[index]):newNode(kind);const n=editing,webs=config.nodes.filter(x=>x.kind==='web');if(n.kind==='judge'&&n.prepared)n.desiredPods??=n.pods;$('editTitle').textContent=(index<0?'添加':'编辑')+(n.kind==='web'?' Web':'评测机');$('editFields').innerHTML=field(n,'id','节点名称')+field(n,'host','服务器 IP / 域名','text','required placeholder="192.168.x.x"')+select(n,'role','角色 / 备用标记',[['active','启用（0）'],['standby','备用（1）']])+field(n,'user','SSH 登录账号')+field(n,'password','SSH 登录密码','password','autocomplete="new-password" placeholder="已有本工具密钥可留空"')+field(n,'sshPort','SSH 端口','number')+field(n,'keyName','本地私钥文件名')+select(n,'elevation','提权方式',[['sudo','sudo · 普通账号密码'],['su','su · root 密码'],['root','SSH 直接登录 root']])+field(n,'elevatePassword','sudo / root 密码','password','autocomplete="new-password" placeholder="sudo 留空时使用登录密码"')+`<label class="wide">服务器项目根目录<input data-key="path" value="${esc(n.path)}"><small>不存在时建立。同机不同节点须使用不同专用目录。</small></label>`+(n.kind==='web'?field(n,'databasePath','初始化数据库目录（可选）','text','placeholder="留空：项目/data/var/mysql/mysql_data"')+`<small class="wide">可指定已挂载 SSD 下的空目录，例如 /data-ssd/mysql/ccpc。已部署节点请在“状态与主备切换 → 数据库存储”检查及迁移；不会因编辑字段直接移动数据。</small>`+field(n,'site','站点标识','text','aria-describedby="siteHelp"')+`<small id="siteHelp" class="wide">站点标识用于容器和目录命名：小写字母开头，仅小写字母和数字，最多 20 位，例如 ccpc。页面显示名称 CCPC 通过 OJ_NAME 配置。</small>`+field(n,'port','Web 端口','number')+field(n,'myadminPort','phpMyAdmin 端口','number')+field(n,'adminUser','初始管理员账号')+field(n,'adminPassword','初始管理员口令','password','autocomplete="new-password" placeholder="留空自动生成"'):select(n,'web','绑定 Web',[['','请选择 Web'],...webs.map(w=>[w.id,w.id])])+(n.prepared?field(n,'desiredPods',`目标 pods（当前配置 ${n.pods}）`,'number','min="1" max="16"'):field(n,'pods','评测容器数 / pods','number','min="1" max="16"'))+field(n,'judgeUser','OJ 评测账号','text','placeholder="留空自动分配 j1、j2…"'));
 if(n.kind==='web')$('editFields').insertAdjacentHTML('beforeend',['sqlRoot','sqlUser','myadmin'].map((k,i)=>field({[k]:n.secrets?.[k]||''},k,['原 MySQL root 口令（接管填写）','原数据库业务口令（接管填写）','原 phpMyAdmin 口令（接管填写）'][i],'password')).join(''));else $('editFields').insertAdjacentHTML('beforeend',field(n,'judgePassword','原评测口令（接管填写）','password'));
 if(n.prepared)for(const key of ['id','role','keyName','path','databasePath','site','port','myadminPort','adminUser','adminPassword','sqlRoot','sqlUser','myadmin','judgePassword','pods','judgeUser']){const f=$('editFields').querySelector(`[data-key="${key}"]`);if(f)f.disabled=true;}
 if(n.prepared){const role=$('editFields').querySelector('[data-key="role"]');const help=document.createElement('small');help.textContent='已部署 / 已接管，角色不能直接修改。启用备用请到“状态与主备切换”；当前主节点转为备用尚未支持。';role.parentElement.append(help);}
 $('nodeDialog').showModal();if(index<0)loadNodeConnections().catch(e=>{notify('读取 SSH 连接失败：'+e.message);});}
function newNode(kind){let i=1,id;do{id=(kind==='web'?'web':'pcj')+String(i++).padStart(2,'0');}while(config.nodes.some(n=>n.id===id));return {id,kind,role:'active',host:'',user:'ccpc00',password:'',elevatePassword:'',sshPort:22,keyName:id,elevation:'sudo',path:'/home/ccpc00/cpc',site:id,port:20080,myadminPort:20050,adminUser:'admin',pods:1,web:config.nodes.find(n=>n.kind==='web')?.id||'',judgeUser:''};}
let nodeAutoSave=false,nodeSaveAgain=false,nodeInputTimer=null,nodeFinishRequested=false;
$('nodeForm').onsubmit=e=>{e.preventDefault();const automatic=nodeAutoSave;nodeAutoSave=false;clearTimeout(nodeInputTimer);nodeInputTimer=null;if(!$('nodeForm').checkValidity()){nodeFinishRequested=false;return;}if(!automatic)nodeFinishRequested=true;guard(async()=>{if(nodeSaveBusy){nodeSaveAgain=true;return;}$('nodeError').hidden=true;$('nodeError').textContent='';nodeSaveBusy=true;const submit=$('nodeForm').querySelector('[type=submit]');submit.disabled=false;submit.textContent='正在保存…';const before=structuredClone(config),beforeSelected=new Set(selected);try{const old=editing.id;for(const input of $('editFields').querySelectorAll('[data-key]')){if(input.disabled)continue;const k=input.dataset.key;if(['adminPassword','sqlRoot','sqlUser','myadmin'].includes(k)){editing.secrets=editing.secrets||{};editing.secrets[k==='adminPassword'?'admin':k]=input.value;}else editing[k]=input.type==='number'?Number(input.value):(['password','elevatePassword'].includes(k)?input.value:input.value.trim());}if(config.nodes.some((n,i)=>i!==editIndex&&n.id===editing.id))throw Error('节点名称重复');if(editIndex>=0)config.nodes[editIndex]=editing;else config.nodes.push(editing);if(old!==editing.id)config.nodes.filter(n=>n.web===old).forEach(n=>n.web=editing.id);if(editIndex>=0&&selected.delete(old))selected.add(editing.id);await save();editIndex=config.nodes.findIndex(n=>n.id===editing.id);editing=structuredClone(config.nodes[editIndex]);for(const k of ['adminPassword','sqlRoot','sqlUser','myadmin']){const input=$('editFields').querySelector(`[data-key="${k}"]`);if(input&&!input.value)input.value=editing.secrets?.[k==='adminPassword'?'admin':k]||'';}if(nodeFinishRequested&&!nodeSaveAgain&&!nodeInputTimer){$('nodeDialog').close();nodeFinishRequested=false;}$('nodeSaveState').textContent='已自动保存到本机';renderNodes();$('confirmPlan').checked=false;if(!automatic)notify('节点已保存到本机，刷新后仍保留。尚未连接或部署服务器。');}catch(error){nodeFinishRequested=false;config=before;selected=beforeSelected;renderNodes();$('nodeSaveState').textContent='未保存：'+error.message;throw error;}finally{nodeSaveBusy=false;submit.disabled=false;submit.textContent='完成';if(nodeSaveAgain&&$('nodeDialog').open){nodeSaveAgain=false;nodeAutoSave=!nodeFinishRequested;$('nodeForm').dispatchEvent(new Event('submit',{cancelable:true}));}}});};
function autoSaveNode(){clearTimeout(nodeInputTimer);nodeInputTimer=null;if(!$('nodeDialog').open||!$('nodeForm').checkValidity())return;if(nodeSaveBusy){nodeSaveAgain=true;return;}nodeAutoSave=true;$('nodeForm').dispatchEvent(new Event('submit',{cancelable:true}));}
$('editFields').addEventListener('change',autoSaveNode);
$('editFields').addEventListener('input',()=>{clearTimeout(nodeInputTimer);$('nodeSaveState').textContent='待自动保存…';nodeInputTimer=setTimeout(autoSaveNode,600);});
$('nodeForm').querySelector('[type=submit]').addEventListener('pointerdown',()=>{if($('nodeForm').checkValidity())nodeFinishRequested=true;});
$('cancelNode').onclick=()=>guard(async()=>{nodeFinishRequested=false;if(nodeInputTimer)autoSaveNode();while(nodeSaveBusy)await new Promise(resolve=>setTimeout(resolve,20));$('nodeDialog').close();});$('addWeb').onclick=()=>editNode(-1,'web');$('addJudge').onclick=()=>editNode(-1,'judge');
function renderNodes(){const webs=config.nodes.filter(n=>n.kind==='web');$('webCount').textContent=webs.length;$('judgeCount').textContent=config.nodes.length-webs.length;$('spareCount').textContent=config.nodes.filter(n=>n.role==='standby').length;$('empty').hidden=!!config.nodes.length;
 $('nodes').innerHTML=config.nodes.map((n,i)=>`<tr class="noderow"><td><strong>${esc(n.id)}</strong></td><td>${n.kind==='web'?'Web':`评测机 · ${n.pods} pods${n.desiredPods&&n.desiredPods!==n.pods?' → '+n.desiredPods:''}`}</td><td>${esc(n.host||'未填写')}</td><td><span class="pill role-${n.role}">${n.role==='active'?'启用 / 0':'备用 / 1'}</span></td><td>${esc(n.user)}</td><td>${n.kind==='judge'?esc(n.web):'—'}</td><td><div class="actions"><button class="secondary small edit" data-index="${i}">编辑</button><button class="secondary danger small remove" data-index="${i}" ${n.prepared?'disabled':''}>移除</button></div></td></tr>`).join('');
 $('nodes').querySelectorAll('.edit').forEach(b=>b.onclick=()=>editNode(+b.dataset.index));$('nodes').querySelectorAll('.remove').forEach(b=>b.onclick=()=>guard(async()=>{const i=+b.dataset.index;if(nodeSaveBusy)throw Error('节点仍在保存，请稍候再移除');if(config.nodes[i].prepared)return;const before=structuredClone(config);selected.delete(config.nodes[i].id);config.nodes.splice(i,1);try{await save();renderNodes();invalidatePlan();notify('节点已移除并保存');}catch(e){config=before;renderNodes();throw e;}}));
 const oldTarget=$('targetNode').value,oldSource=$('sourceWeb').value;$('targetNode').innerHTML=config.nodes.map(n=>`<option value="${esc(n.id)}">${esc(n.id)} · ${n.role==='active'?'启用':'备用'}</option>`).join('');$('sourceWeb').innerHTML='<option value="">选择迁移来源主 Web</option>'+webs.filter(w=>w.role==='active').map(w=>`<option value="${esc(w.id)}">${esc(w.id)}</option>`).join('');if(config.nodes.some(n=>n.id===oldTarget))$('targetNode').value=oldTarget;if(webs.some(n=>n.id===oldSource&&n.role==='active'))$('sourceWeb').value=oldSource;
 $('deploySelection').innerHTML=config.nodes.map(n=>`<label><input type="checkbox" value="${esc(n.id)}" ${selected.has(n.id)?'checked':''}>${esc(n.id)} · ${n.role==='standby'?'备用':'启用'}</label>`).join('');$('deploySelection').querySelectorAll('input').forEach(x=>x.onchange=()=>{x.checked?selected.add(x.value):selected.delete(x.value);invalidatePlan();});

 if(typeof renderBatchRows==='function')renderBatchRows();
 $('backups').textContent=(config.backups||[]).map(b=>`${b.web} / ${b.version}\n${b.path}`).join('\n\n')||'暂无备份记录。';}
function sync(){$('version').value=$('imageVersion').value.trim();$('mirror').value=$('imageMirror').value.trim();for(const k of ['version','toolVersion','mirror'])config[k]=$(k).value.trim();for(const k of ['disableGui','neverSleep','performance'])config[k]=$(k).checked;return config;}
function load(c,all=false){if(!all&&typeof batchSaveTimer!=='undefined'&&batchSaveTimer){for(const n of c.nodes){const live=config.nodes.find(x=>x.id===n.id);if(live)n.targetVersion=live.targetVersion;}}config=c;if(all)selected=new Set();else selected=new Set([...selected].filter(id=>c.nodes.some(n=>n.id===id)));for(const k of ['version','toolVersion','mirror'])$(k).value=c[k]||'';for(const k of ['disableGui','neverSleep','performance'])$(k).checked=!!c[k];if(all){$('imageMirror').value=c.mirror||'';const u=c.uiChoices||{};for(const k of ['imageVersion','releaseVersion','updateVersion'])$(k).value=u[k]||'';if(u.imageKind)$('imageKind').value=u.imageKind;if(u.imageSource)$('imageSource').value=u.imageSource;$('imageDependencies').checked=false;$('imageMirrorField').hidden=$('imageSource').value!=='custom';}renderNodes();showTool();}
function showTool(){
 const t=config.tool;
 if(!t){$('toolCurrent').textContent='当前部署将使用：尚未选择部署程序';$('toolCurrent').dataset.state='empty';$('toolInfo').textContent='尚未选择 · Linux x86_64 可执行文件';return;}
 const mismatch=t.version&&t.version!==$('toolVersion').value.trim();
 $('toolCurrent').textContent=mismatch?`当前已选：${t.version} · 与填写的 Release 不一致，暂不能部署`:t.version?`当前部署将使用：${t.version}`:'当前部署将使用：所选文件（版本未知）';
 $('toolCurrent').dataset.state=mismatch?'mismatch':t.version?'ready':'unknown';
 $('toolInfo').textContent=[mismatch?'版本不一致：请重新下载，或把 Release 改回 '+t.version:'Windows 已缓存',t.version?`部署程序版本：${t.version} · 文件：${t.name||'旧记录未保存文件名'}`:'手动选择或旧缓存：版本未记录，请核对文件来源',`${(t.size/1048576).toFixed(1)} MB · SHA256 ${t.sha256}`,t.localPath?'本地缓存：'+t.localPath:'',t.source?'来源：'+(t.source==='local'?'本地上传':t.source):'',t.versionSource==='filename'?'版本来自文件名，未执行上传的程序。':''].filter(Boolean).join('\n');
}
function invalidatePlan(){planReady=false;$('confirmPlan').checked=false;$('execute').disabled=true;$('planRows').innerHTML='<p class="hint">'+(selected.size?'已更改选择，请同步服务器版本并指定目标版本。':'尚未选择节点，请先勾选需要部署的节点。')+'</p>';}
let configSaving=Promise.resolve();
function save(){configSaving=configSaving.catch(()=>{}).then(performSave);return configSaving;}
async function performSave(){if(preferencesTimer)autoSaveSettings();await preferencesQueue;const submitted=structuredClone(sync());const r=await api('save',{config:submitted});for(const n of r.config.nodes){const live=config.nodes.find(x=>x.id===n.id),sent=submitted.nodes.find(x=>x.id===n.id);if(live&&sent&&live.targetVersion!==sent.targetVersion)n.targetVersion=live.targetVersion;}const liveSettings=sync();for(const key of ['version','toolVersion','mirror','disableGui','neverSleep','performance'])if(liveSettings[key]!==submitted[key])r.config[key]=liveSettings[key];load(r.config);$('planRows').className='';const chosen=r.plan.filter(n=>selected.has(n.id));planReady=chosen.length>0;$('execute').disabled=running||!planReady;$('planRows').innerHTML=chosen.length?chosen.map(n=>`<article class="planrow"><h3>${esc(n.id)} <span class="pill">${n.role==='active'?'正式部署':'准备备用'}</span></h3><p>${esc(n.host)} · <code>${esc(n.path)}</code></p><ol>${n.steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol></article>`).join(''):'<p class="hint">尚未选择节点，请先勾选需要部署的节点。</p>';return r;}
$('save').onclick=()=>autoSaveSettings();$('review').onclick=$('refreshPlan').onclick=()=>guard(async()=>{await save();$('confirmPlan').checked=false;tab('plan');});
async function start(action,ids,source){await save();await api('run',{action,config,selected:ids,source});tab(action==='deploy'?'plan':action==='distribute'?'artifacts':'monitor');await poll();}
$('execute').onclick=()=>guard(async()=>{if(!planReady)throw Error('请先选择节点并更新计划');if(!$('confirmPlan').checked)throw Error('请先核对并勾选执行计划');if(!selected.size)throw Error('请选择至少一个节点');await start('deploy',[...selected]);});
$('health').onclick=()=>guard(()=>start('health'));$('healthOne').onclick=()=>guard(()=>start('health',[$('targetNode').value]));$('stop').onclick=()=>guard(async()=>{await api('cancel',{});notify('已请求在节点边界停止；数据保护和迁移恢复步骤完成后再返回。');});
for(const action of ['backup','promote','restoreHost','rebind','resizeJudge'])$(action).onclick=()=>guard(async()=>{if(!$('confirmSwitch').checked)throw Error('请先确认维护操作');await start(action,[$('targetNode').value],$('sourceWeb').value);});
$('fetchVersions').onclick=()=>guard(async()=>{const n=config.nodes.find(n=>n.id===$('targetNode').value);if(!n)throw Error('先选择操作节点');$('fetchVersions').disabled=true;try{const r=await api('image-tags',{kind:n.kind});$('imageTags').innerHTML=r.tags.map(x=>`<option value="${esc(x.name)}">${esc(x.updated)}</option>`).join('');$('versionNotice').textContent=r.notice+' 可选：'+r.tags.slice(0,12).map(x=>x.name).join('、');}finally{$('fetchVersions').disabled=false;}});
for(const action of ['adoptInspect','adopt'])$(action).onclick=()=>guard(()=>start(action,[$('targetNode').value]));
for(const action of ['checkUpdate','upgrade','recoverUpdate'])$(action).onclick=()=>guard(async()=>{if(action!=='checkUpdate'&&!$('confirmSwitch').checked)throw Error('请先确认维护操作');if(action!=='recoverUpdate'){const v=$('updateVersion').value.trim();if(!v)throw Error('填写本次目标镜像标签');$('version').value=v;$('imageVersion').value=v;}await start(action,[$('targetNode').value]);});
async function download(path,name){const r=await fetch('/api/'+path,{headers:{'X-CSG-Token':token}});if(!r.ok)throw Error('下载失败');const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
$('exportCredentials').onclick=()=>guard(()=>download('export','csgoj-deployment-credentials.json'));$('exportLog').onclick=()=>guard(()=>download('log','deployment.log'));$('template').onclick=$('template2').onclick=()=>guard(()=>download('template','CSGOJ节点导入模板.xlsx'));
$('xlsxFile').onchange=()=>guard(async()=>{const f=$('xlsxFile').files[0];if(!f)return;if(f.size>8*1024*1024)throw Error('XLSX 不能超过 8 MB');const a=new Uint8Array(await f.arrayBuffer());let raw='';for(let i=0;i<a.length;i+=32768)raw+=String.fromCharCode(...a.subarray(i,i+32768));const r=await api('xlsx-preview',{file:btoa(raw),config:sync()});importCandidate=r.config;$('importNotice').textContent=r.notice;$('importSettings').textContent=`镜像 ${r.config.version} · 关闭图形界面 ${r.config.disableGui?'是':'否'} · 永不休眠 ${r.config.neverSleep?'是':'否'} · 性能模式 ${r.config.performance?'是':'否'}`;$('importRows').innerHTML=r.changes.map(n=>`<tr><td>${esc(n.action)}</td><td>${esc(n.id)}</td><td>${esc(n.kind)}</td><td>${esc(n.host)}</td><td>${n.role==='standby'?'1 · 备用':'0 · 启用'}</td></tr>`).join('');$('importDialog').showModal();$('xlsxFile').value='';});
$('cancelImport').onclick=()=>{$('importDialog').close();importCandidate=null;};$('confirmImport').onclick=()=>guard(async()=>{const r=await api('save',{config:importCandidate});load(r.config,true);invalidatePlan();$('importDialog').close();importCandidate=null;tab('configure');notify('导入配置已显示并保存。尚未连接服务器；请核对后到“批量更新”同步版本、选择节点及目标版本。');});
async function poll(){
 const s=await api('state'),was=running;running=s.running||Object.values(s.activity||{}).some(Boolean);
 if(typeof renderDistributionState==='function')renderDistributionState(s);
 if(typeof renderBatchState==='function')renderBatchState(s);
 if(typeof renderDatabaseState==='function')renderDatabaseState(s);
 $('jobState').textContent=s.status;$('logs').textContent=s.logs.join('\n')||'尚未执行任务。';$('logs').scrollTop=$('logs').scrollHeight;
 $('statuses').innerHTML=config.nodes.map(n=>{
  const state=s.nodes[n.id];
  return `<article class="statuscard"><h3>${esc(n.id)} <span class="pill">${n.role==='active'?'启用':'备用'}</span></h3><p>${esc(n.host)} · ${n.kind==='web'?'Web':'评测机'}</p><p>版本：${esc(n.deployedVersion||n.preparedVersion||'尚未部署')}</p>${state?.adoption?`<p>接管检查：${esc(state.adoption.status)} · 原版本 ${esc(state.adoption.version)}</p>`:''}${state?.update?`<p>更新检查：${esc(state.update.status)} · 目标 ${esc(state.update.targetVersion)}</p>`:''}<p>服务器：<strong>${esc(state?.serverStatus||'未检测')}</strong></p><p>${n.kind==='web'?'Web 服务':'评测服务'}：<strong>${esc(state?.serviceStatus||'未检测')}</strong></p>${n.kind==='judge'?`<p>检测时运行 pods：<strong>${esc(state?.runningPods??'未检测')}</strong></p>`:''}${state?.checkedAt?`<small>检测时间：${esc(state.checkedAt)}</small>`:''}${state?.canStart&&n.role==='active'?`<div class="spaced"><button class="startservice" data-node="${esc(n.id)}" ${running?'disabled':''}>一键启动${n.kind==='web'?' Web':'评测服务'}</button></div>`:''}</article>`;
 }).join('')||'<p>请先添加节点。</p>';
 remoteControlState=s;renderRemoteControls();
 if(s.job&&!s.running&&s.job!==remoteSeenJob){const r=await api('config');load(r.config);remoteSeenJob=s.job;notify('任务状态：'+s.status+'。详情见日志。');}
}
let remoteControlState={nodes:{}};
function renderRemoteControls(){const s=remoteControlState;
 for(const id of ['adoptInspect','adopt','fetchVersions','checkUpdate','upgrade','recoverUpdate','execute','health','healthOne','backup','promote','restoreHost','rebind','resizeJudge','save','review','refreshPlan','addWeb','addJudge','confirmImport'])$(id).disabled=running;
 $('execute').disabled=running||!planReady||!selected.size;
 $('adopt').disabled=running||!s.nodes[$('targetNode').value]?.adoption||!!config.nodes.find(n=>n.id===$('targetNode').value)?.prepared;
 $('configure').querySelectorAll('input,select,button').forEach(x=>x.disabled=running);$('nodes').querySelectorAll('.remove').forEach(b=>b.disabled=running||!!config.nodes[+b.dataset.index].prepared);
}
$('targetNode').addEventListener('change',()=>poll().catch(e=>notify(e.message)));
$('statuses').onclick=e=>{const button=e.target.closest('.startservice');if(button&&!running)guard(()=>start('startService',[button.dataset.node]));};
for(const id of ['disableGui','neverSleep','performance'])$(id).addEventListener('change',()=>{invalidatePlan();autoSaveSettings();});
$('toolVersion').addEventListener('input',showTool);
guard(async()=>{if(!token)throw Error('请通过 Windows 启动程序打开本地页面。');const r=await api('config');load(r.config,true);invalidatePlan();$('dataDir').textContent=r.dataDir;await poll();tab('sshModule');document.dispatchEvent(new Event('console-ready'));setInterval(()=>poll().catch(e=>notify('本地服务连接中断：'+e.message)),2000);});
window.addEventListener('beforeunload',e=>{if(running||preferencesPending||preferencesTimer||nodeSaveBusy||nodeInputTimer){e.preventDefault();e.returnValue='仍有任务或配置正在保存';}});

async function loadNodeConnections(){
 const r=await api('ssh/profiles');
 if(!$('nodeDialog').open||editIndex>=0)return;
 const available=r.profiles.filter(p=>!config.nodes.some(n=>n.keyName===p.keyName));
 const box=document.createElement('label');box.className='wide';box.textContent='复用已导入的 SSH 连接';
 const selectEl=document.createElement('select');selectEl.id='nodeConnection';
 selectEl.innerHTML='<option value="">手动填写 / 选择已有连接</option>'+available.map((p,i)=>`<option value="${i}">${esc(p.keyName)} · ${esc(p.host)} · ${esc(p.user)}</option>`).join('');
 box.append(selectEl);$('editFields').prepend(box);
 selectEl.onchange=()=>{if(selectEl.value==='')return;const p=available[Number(selectEl.value)];for(const k of ['host','user','sshPort','keyName','password']){const el=$('editFields').querySelector(`[data-key="${k}"]`);if(el)el.value=p[k]??'';}const id=$('editFields').querySelector('[data-key="id"]');if(!config.nodes.some(n=>n.id===p.keyName))id.value=p.keyName;$('editFields').querySelector('[data-key="elevation"]').value=p.user==='root'?'root':'sudo';};
}

let credentialNodes=[], credentialSelection=new Set();
function renderCredentialNodes(){
 $('credentialRows').innerHTML=credentialNodes.map(n=>`<tr><td><input type="checkbox" class="credential-select" value="${esc(n.id)}" aria-label="导出 ${esc(n.id)}" ${credentialSelection.has(n.id)?'checked':''}></td><td>${esc(n.id)}</td><td>${n.kind==='web'?'Web':'评测机'}</td><td>${esc(n.host)}</td><td>${n.role==='standby'?'备用':'启用'}</td><td>${n.deployed?'已部署 / 已接管':'未记录已部署'}</td></tr>`).join('')||'<tr><td colspan="6">暂无已保存部署节点。</td></tr>';
 $('credentialCount').textContent=`已保存 ${credentialNodes.length} 个节点，已选 ${credentialSelection.size} 个；在线状态请在“状态与主备切换”检测。`;
 $('exportSelectedTxt').disabled=!credentialSelection.size;
 $('exportAllTxt').disabled=!credentialNodes.length;
 document.querySelectorAll('.credential-select').forEach(e=>e.onchange=()=>{if(e.checked)credentialSelection.add(e.value);else credentialSelection.delete(e.value);renderCredentialNodes();});
}
async function refreshCredentialNodes(){const r=await api('config');credentialNodes=r.config.nodes;credentialSelection=new Set([...credentialSelection].filter(id=>credentialNodes.some(n=>n.id===id)));renderCredentialNodes();}
async function exportCredentialTxt(all){
 const ids=all?null:[...credentialSelection];if(ids&&!ids.length)throw Error('请先勾选节点');
 const r=await fetch('/api/credentials-export',{method:'POST',headers:{'X-CSG-Token':token,'Content-Type':'application/json'},body:JSON.stringify({ids})});
 if(!r.ok){const j=await r.json();throw Error(j.error||'导出失败');}
 const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=`csgoj-credentials-${all?'all':'selected'}-${new Date().toISOString().replace(/[:.]/g,'-')}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
 notify(`已生成${all?'全部':'勾选节点'}业务口令 TXT，浏览器将保存到本地下载目录。`);
}
$('exportAllTxt').onclick=()=>guard(()=>exportCredentialTxt(true));
$('exportSelectedTxt').onclick=()=>guard(()=>exportCredentialTxt(false));
$('credentialSelectAll').onclick=()=>{credentialSelection=new Set(credentialNodes.map(n=>n.id));renderCredentialNodes();};
$('credentialClear').onclick=()=>{credentialSelection.clear();renderCredentialNodes();};

// Serialize preference writes; never replace fields the operator is still editing.
let preferencesQueue=Promise.resolve(), preferencesPending=0;
function autoSaveSettings(){
 clearTimeout(preferencesTimer);preferencesTimer=null;
 invalidatePlan();preferencesPending++;$('settingsSaveState').textContent='正在保存…';$('autoSaveStatus').textContent='正在保存…';
 const settings={version:$('imageVersion').value.trim(),mirror:$('imageMirror').value.trim(),uiChoices:{}};
 for(const k of ['disableGui','neverSleep','performance'])settings[k]=$(k).checked;
 for(const k of ['releaseVersion','imageVersion','updateVersion','imageKind','imageSource'])settings.uiChoices[k]=$(k).value.trim();
 settings.uiChoices.imageDependencies=$('imageDependencies').checked;
 preferencesQueue=preferencesQueue.catch(()=>{}).then(async()=>{
  try{await api('preferences',{settings});Object.assign(config,settings);$('settingsSaveState').textContent='已自动保存到本机';$('autoSaveStatus').textContent='已自动保存到本机';$('save').hidden=true;}
  catch(e){$('settingsSaveState').textContent='未保存：'+e.message;$('autoSaveStatus').textContent='未保存，请检查提示';$('save').hidden=false;notify('配置未保存：'+e.message);}
  finally{preferencesPending--;}
 });return preferencesQueue;
}
for(const id of ['releaseVersion','imageVersion','updateVersion','imageMirror','imageKind','imageSource','imageDependencies'])$(id).addEventListener('change',autoSaveSettings);

let preferencesTimer=null;
for(const id of ['releaseVersion','imageVersion','updateVersion','imageMirror'])$(id).addEventListener('input',()=>{clearTimeout(preferencesTimer);$('autoSaveStatus').textContent='待自动保存…';preferencesTimer=setTimeout(autoSaveSettings,600);});
