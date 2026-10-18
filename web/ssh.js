'use strict';
let sshProfiles=[],sshBusy=false,sshLoaded=false,sshScanning=false,sshImporting=false,sshSeenJob=null;
const sshSelection=()=>Array.from(document.querySelectorAll('#sshRows .sshselect:checked')).map(x=>sshProfiles[+x.dataset.index].keyName);
function renderSSH(){
 const checked=new Set([...document.querySelectorAll('#sshRows .sshselect:checked')].map(el=>el.closest('tr').querySelector('[data-ssh=keyName]').value));
 $('sshRows').innerHTML=sshProfiles.map((p,i)=>`<tr data-index="${i}"><td><input type="checkbox" class="sshselect" data-index="${i}" aria-label="选择 ${esc(p.keyName)}"></td><td><input data-ssh="keyName" value="${esc(p.keyName)}" ${p.saved?'readonly':''} aria-label="私钥文件名"><small>${p.exists?'本地密钥已存在（服务器授权需验证）':'尚未生成或导入'}</small></td><td><input data-ssh="host" value="${esc(p.host)}" aria-label="服务器 IP"></td><td><input data-ssh="user" value="${esc(p.user)}" aria-label="SSH 登录账号"></td><td><input data-ssh="sshPort" type="number" min="1" max="65535" value="${p.sshPort||22}" aria-label="SSH 端口"></td><td><input data-ssh="password" type="password" value="${esc(p.password)}" autocomplete="new-password" placeholder="首次配置公钥时需要" aria-label="SSH 登录密码"></td><td class="sshresult">未检测</td></tr>`).join('');
 document.querySelectorAll('#sshRows .sshselect').forEach(el=>el.checked=checked.has(sshProfiles[+el.dataset.index].keyName));
 $('sshKeyPaths').textContent=sshProfiles.filter(p=>p.localPath).map(p=>`${p.keyName} → ${p.localPath}${p.fingerprint?'\n公钥指纹：'+p.fingerprint:''}${p.keyError?'\n'+p.keyError:''}`).join('\n\n')||'生成或导入后显示本机私钥路径。';
}
async function refreshSSH(){const r=await api('ssh/profiles');sshProfiles=r.profiles.map(p=>({...p,saved:true}));sshLoaded=true;renderSSH();await pollSSH();}
function collectSSH(){document.querySelectorAll('#sshRows tr').forEach(row=>row.querySelectorAll('[data-ssh]').forEach(input=>{sshProfiles[+row.dataset.index][input.dataset.ssh]=input.dataset.ssh==='sshPort'?Number(input.value):input.dataset.ssh==='password'?input.value:input.value.trim();}));return sshProfiles;}
async function saveSSH(quiet=false){collectSSH();const r=await api('ssh/save',{profiles:sshProfiles});const checked=new Set(sshSelection());if(!quiet){sshProfiles=r.profiles.map(p=>({...p,saved:true}));renderSSH();}document.querySelectorAll('#sshRows .sshselect').forEach(e=>e.checked=checked.has(sshProfiles[+e.dataset.index].keyName));const c=await api('config');if(c.config.nodes.length)load(c.config);await pollSSH();}
async function runSSH(action){
 clearTimeout(sshInputTimer);sshInputTimer=null;await sshSaveQueue;
 collectSSH();const names=sshSelection();if(!names.length)throw Error('请勾选要操作的 SSH 连接');
 await saveSSH();await api('ssh/run',{action,names});await pollSSH();
}
function renderSSHControls(){
 document.querySelectorAll('#sshModule input,#sshModule button,#sshImportDialog button').forEach(e=>e.disabled=sshBusy||running||(e.id==='sshScan'&&sshScanning)||(e.id==='sshConfirmImport'&&(sshScanning||sshImporting||!document.querySelector('.sshimport:checked'))));
}
async function pollSSH(){
 if(!sshLoaded)return;const s=await api('ssh/state'),was=sshBusy;sshBusy=s.running;
 $('sshJobState').textContent=s.status;$('sshLogs').textContent=s.logs.join('\n')||'尚未执行 SSH 操作。';
 renderSSHControls();
 document.querySelectorAll('#sshRows tr').forEach(row=>{const p=sshProfiles[+row.dataset.index],r=s.results[p.keyName];row.querySelector('.sshresult').textContent=r&&r.host===p.host&&r.user===p.user&&r.sshPort===p.sshPort?r.status:'未检测';});
 if(!sshBusy&&s.job&&s.job!==sshSeenJob){sshBusy=true;const checked=new Set(sshSelection());const r=await api('ssh/profiles');sshProfiles=r.profiles.map(p=>({...p,saved:true}));renderSSH();document.querySelectorAll('#sshRows .sshselect').forEach(e=>e.checked=checked.has(sshProfiles[+e.dataset.index].keyName));sshSeenJob=s.job;sshBusy=false;}
}
document.querySelector('[data-tab="sshModule"]').addEventListener('click',()=>guard(()=>refreshSSH()));
$('sshRefresh').onclick=()=>guard(()=>refreshSSH());
$('sshSave').onclick=()=>guard(async()=>{await saveSSH();notify('连接和部署节点已保存；已有私钥的别名已同步到 Windows .ssh/config。私钥不变，未连接服务器；尚无私钥的连接请先生成或导入密钥。');});
$('sshAdd').onclick=()=>{collectSSH();let i=1;while(sshProfiles.some(p=>p.keyName==='ssh'+String(i).padStart(2,'0')))i++;sshProfiles.push({keyName:'ssh'+String(i).padStart(2,'0'),host:'',user:'ccpc00',sshPort:22,password:'',exists:false});renderSSH();};
for(const [id,action] of [['sshGenerate','generate'],['sshAuthorize','authorize'],['sshTest','test'],['sshPasswordTest','testPassword']])$(id).onclick=()=>guard(()=>runSSH(action));
$('sshScan').onclick=()=>guard(async()=>{if(sshScanning)return;sshScanning=true;$('sshImportRows').innerHTML='<tr><td colspan="6" role="status">正在扫描本机 SSH 配置和密钥，请稍候…</td></tr>';$('sshConfirmImport').disabled=true;$('sshImportDialog').showModal();try{const r=await api('ssh/inventory');$('sshImportRows').innerHTML=r.profiles.map(p=>`<tr><td><input class="sshimport" type="checkbox" value="${esc(p.keyName)}" ${p.exists&&!p.keyError?'checked':'disabled'} aria-label="导入 ${esc(p.keyName)}"></td><td>${esc(p.keyName)}</td><td><input data-import="host" value="${esc(p.host)}" placeholder="填写服务器 IP" aria-label="导入服务器 IP"></td><td><input data-import="sshPort" type="number" value="${p.sshPort}" aria-label="导入 SSH 端口"></td><td><input data-import="user" value="${esc(p.user)}" aria-label="导入 SSH 账号"></td><td>${esc(p.sourcePath)}<br><small>${esc(p.keyError||p.fingerprint||'未找到私钥')}</small></td></tr>`).join('')||'<tr><td colspan="6">未找到 web / pcj 前缀的连接或密钥，可直接添加连接。</td></tr>';}finally{sshScanning=false;$('sshConfirmImport').disabled=!document.querySelector('.sshimport:checked');}});
$('sshCancelImport').onclick=()=>$('sshImportDialog').close();
$('sshConfirmImport').onclick=()=>guard(async()=>{if(sshImporting||sshScanning||!document.querySelector('.sshimport:checked'))return;sshImporting=true;$('sshConfirmImport').disabled=true;$('sshConfirmImport').textContent='正在导入，请稍候…';try{const names=Array.from(document.querySelectorAll('.sshimport:checked')).map(x=>x.value);const connections=Array.from(document.querySelectorAll('.sshimport:checked')).map(x=>{const row=x.closest('tr');return {keyName:x.value,host:row.querySelector('[data-import=host]').value.trim(),user:row.querySelector('[data-import=user]').value.trim(),sshPort:Number(row.querySelector('[data-import=sshPort]').value)};});await api('ssh/import',{names,connections});$('sshImportDialog').close();await refreshSSH();notify('已将所选密钥复制到本机工具资料目录，保留原文件；未连接或修改服务器。');}finally{sshImporting=false;$('sshConfirmImport').disabled=!document.querySelector('.sshimport:checked');$('sshConfirmImport').textContent='确认导入所选记录';}});
setInterval(()=>{if(sshLoaded)pollSSH().catch(e=>notify('SSH 模块：'+e.message));},2000);

let sshSaveQueue=Promise.resolve();
$('sshRows').addEventListener('change',e=>{
 if(!e.target.matches('[data-ssh]'))return;
 $('sshSaveState').textContent='正在保存连接…';
 sshSaveQueue=sshSaveQueue.catch(()=>{}).then(async()=>{try{await saveSSH(true);$('sshSaveState').textContent='已自动保存并同步本机 SSH';$('sshSave').hidden=true;}catch(error){$('sshSaveState').textContent='未保存：'+error.message;$('sshSave').hidden=false;}});
});
document.addEventListener('console-ready',()=>guard(refreshSSH));

let sshInputTimer=null;
$('sshRows').addEventListener('input',e=>{if(!e.target.matches('[data-ssh]'))return;clearTimeout(sshInputTimer);$('sshSaveState').textContent='待自动保存…';const input=e.target;sshInputTimer=setTimeout(()=>{sshInputTimer=null;input.dispatchEvent(new Event('change',{bubbles:true}));},600);});

$('sshImportRows').addEventListener('change',()=>{$('sshConfirmImport').disabled=sshScanning||sshImporting||!document.querySelector('.sshimport:checked');});
