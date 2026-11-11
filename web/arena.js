'use strict';
// Both pages share one vault so every IP range binds to an actual server row.
let arenaConfig=null,arenaLoading=null,arenaBusy=false,arenaDirty=false,arenaCachedJob=null;
let arenaReleaseList=null,arenaReleaseListPending=null,arenaReleaseApplying=false;
const arenaRoles=['server','client'];
const arenaPage=role=>'arena'+(role==='server'?'Server':'Client');
const arenaTitle=role=>role==='server'?'管控服务端':'管控选手端';
function arenaConsoleLinks(servers) {
 for(const box of document.querySelectorAll('[data-arena-consoles]')) {
  box.replaceChildren();
  for(const s of servers) {
   if(s.scheme!=='https' || !/^\d{1,3}(\.\d{1,3}){3}$/.test(s.host) || s.host.split('.').some(n=>Number(n)>255))continue;
   const port=Number(s.servicePort||39721);
   if(!Number.isInteger(port)||port<1||port>65534)continue;
   const link=document.createElement('a');
   link.href='https://'+s.host+':'+port+'/';link.target='_blank';link.rel='noopener noreferrer';
   link.className='button secondary';link.textContent=(s.name||'场控服务端')+' · '+s.host+':'+port+' ↗';
   box.append(link);
  }
  if(!box.childElementCount)box.textContent='请先在「管控服务端」保存 HTTPS 业务地址。';
 }
}
function arenaHandoff(page,role) {
 const panel=document.createElement('section');panel.className='panel arena-handoff';
 if(role==='server') {
  panel.innerHTML='<h2>部署场控服务端</h2><p>添加服务端地址，保存并预览目标，再点击「一键部署并启动」。查询服务状态后，打开对应控制台继续部署选手机。</p><div class="actions" data-arena-consoles></div>';
 } else {
  panel.innerHTML='<h2>从场控服务端部署选手机</h2><p>以下步骤适用于 CSGArena v1.16.2。首次部署先在服务端本机初始化管理员，再登录对应控制台。</p><div class="actions" data-arena-consoles></div><ol><li><strong>准备选手机环境</strong><p>在「环境与镜像」发布对应平台的场控客户端。需要自动登记的机位须已运行本场引导程序，并配置服务端 HTTPS 地址和证书指纹。</p></li><li><strong>从服务端分发</strong><p>在场控「批量部署」添加选手机 IP 段和登录凭据，检查 Ansible 环境，选择与公开版本一致的客户端单文件并分发。</p></li><li><strong>核对机位上线</strong><p>先看分发回执，再到「机位总览」核对新心跳。未预置引导程序的机器只会收到文件。</p></li></ol><button type="button" class="secondary" data-open-server>配置场控服务端</button>';
  panel.querySelector('[data-open-server]').onclick=()=>document.querySelector('nav [data-tab="arenaServer"]').click();
  const legacy=document.createElement('details');legacy.id='arenaDirectClient';legacy.className='panel';
  const summary=document.createElement('summary');summary.textContent='通过 DT 直接部署选手端（兼容流程）';
  legacy.append(summary,...Array.from(page.children));page.append(legacy);
 }
 page.prepend(panel);
}
function arenaInput(key,label,value='',type='text',extra=''){
 return `<label>${label}<input data-arena="${key}" type="${type}" value="${esc(value)}" ${extra}></label>`;
}
function arenaSelect(key,label,value,options){
 return `<label>${label}<select data-arena="${key}">${options.map(([v,t])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
}
function arenaRead(){
 const c=structuredClone(arenaConfig);
 for(const role of arenaRoles){
  c[role+'s']=[...$(arenaPage(role)+'Rows').querySelectorAll('.arena-row')].map(row=>{
   const n={id:row.dataset.id};
   for(const el of row.querySelectorAll('[data-arena]'))n[el.dataset.arena]=el.type==='number'&&el.value!==''?Number(el.value):el.value;
   if(role==='client')n.prefix=[...row.querySelectorAll('[data-octet]')].map(el=>el.value===''?'':Number(el.value));
   return n;
  });
  for(const os of ['linux','windows']){
   const key=role+'-'+os;c.artifacts[key]={};
   for(const el of $(arenaPage(role)).querySelectorAll(`[data-artifact="${key}"]`))c.artifacts[key][el.dataset.field]=el.value.trim();
  }
 }
 c.distro=$('arenaDistro').value.trim();c.releaseTag=document.querySelector('[data-arena-release]').value.trim();return c;
}
function arenaBindOptions(){
 const servers=[...$('arenaServerRows').querySelectorAll('.arena-row')].map(row=>({id:row.dataset.id,name:row.querySelector('[data-arena="name"]').value,host:row.querySelector('[data-arena="host"]').value}));
 for(const el of $('arenaClientRows').querySelectorAll('[data-arena="server"]')){
  const value=el.value;el.innerHTML='<option value="">请选择服务端</option>'+servers.map(s=>`<option value="${esc(s.id)}">${esc(s.name||s.host||s.id)} · ${esc(s.host)}</option>`).join('');el.value=value;
 }
 if(typeof arenaScanBindings==='function')arenaScanBindings();
 const bulk=$('arenaBulkServer');if(bulk){const selected=bulk.value;bulk.innerHTML='<option value="">请选择服务端</option>'+arenaConfig.servers.map(s=>`<option value="${esc(s.id)}">${esc(s.name||s.host||s.id)}</option>`).join('');bulk.value=selected;}
}
function arenaRows(role){
 const rows=arenaConfig[role+'s'];
 $(arenaPage(role)+'Rows').innerHTML=rows.length?rows.map(n=>{
  const range=role==='client'?`<div class="arena-range"><span>IP 段</span><div class="arena-range-boxes"><div class="arena-address">${n.prefix.map((p,i)=>`<input type="number" min="0" max="255" data-octet="${i}" value="${esc(p)}" aria-label="起始 IP 第 ${i+1} 段"><b>.</b>`).join('')}<input type="number" min="1" max="254" data-arena="start" value="${esc(n.start)}" aria-label="起始 IP 尾段"></div><b>—</b><div class="arena-address">${n.prefix.map((p,i)=>`<input value="${esc(p)}" data-mirror="${i}" readonly aria-label="结束 IP 第 ${i+1} 段（自动同步）"><b>.</b>`).join('')}<input type="number" min="1" max="254" data-arena="end" value="${esc(n.end)}" aria-label="结束 IP 尾段"></div></div></div>`:'';
  return `<article class="arena-row" data-id="${esc(n.id)}"><div class="sectionhead"><strong>${role==='server'?'单台服务端':'选手端 IP 段'}</strong><button class="secondary danger" data-remove="${esc(n.id)}">移除</button></div>${range}<div class="nodefields">`+
   arenaInput('name','名称',n.name)+arenaSelect('os','系统（x64）',n.os,[['linux','Linux · SSH'],['windows','Windows · WinRM / SSH']])+
   (role==='server'?arenaInput('host','单个 IPv4 地址',n.host,'text','placeholder="192.168.1.10"'):'')+
   arenaInput('port','登录端口（可留空）',n.port,'number','min="1" max="65535" placeholder="SSH 22 / WinRM 5986"')+
   arenaInput('user','登录用户名',n.user,'text','autocomplete="off"')+arenaInput('password','登录密码',n.password,'password','autocomplete="new-password"')+arenaInput('privateKeyPath','私钥绝对路径（可选）',n.privateKeyPath||'','text','placeholder="填写后仅使用此私钥登录"')+arenaInput('keyPassphrase','私钥解锁口令（可选）',n.keyPassphrase||'','password','autocomplete="new-password"')+
   `<div data-linux-fields ${n.os==='windows'?'hidden':''}>${arenaSelect('elevation','Linux 提权方式',n.elevation,[['sudo','sudo（普通账号）'],['su','su（root）']])}${arenaInput('rootPassword','sudo / root 提权密码',n.rootPassword,'password','autocomplete="new-password" placeholder="留空使用登录密码；root 登录不提权"')}</div>`+
   (role==='server'?arenaInput('servicePort','管控业务端口',n.servicePort,'number','min="1" max="65534"')+arenaSelect('scheme','管控业务协议',n.scheme,(n.scheme==='http'?[['http','HTTP（旧配置，请切换 HTTPS）'],['https','HTTPS']]:[['https','HTTPS']])):arenaSelect('server','自动登记到服务端',n.server,[['','请选择服务端'],...arenaConfig.servers.map(s=>[s.id,(s.name||s.host||s.id)+' · '+s.host])]))+
   '</div><p class="hint">填写私钥路径后通过 SSH 登录；Windows 使用私钥需开启 OpenSSH、将默认 Shell 配置为 PowerShell，并使用管理员账号。Windows 密码登录仍走 WinRM HTTPS；Linux 需 Python 3 和 systemd。登录端口和管控业务端口相互独立。</p></article>';
 }).join(''):'<p class="empty">暂无目标，点击上方按钮添加。</p>';
}
function arenaRender(){
 $('arenaDistro').value=arenaConfig.distro;
 for(const el of document.querySelectorAll('[data-arena-release]'))el.value=arenaConfig.releaseTag||'v1.16.2';
 for(const role of arenaRoles){
  arenaRows(role);
  for(const os of ['linux','windows']){
   const key=role+'-'+os,a=arenaConfig.artifacts[key]||{};
   for(const el of $(arenaPage(role)).querySelectorAll(`[data-artifact="${key}"]`))el.value=a[el.dataset.field]||'';
  }
 }
 if(typeof arenaScanBindings==='function')arenaScanBindings();
 const bulk=$('arenaBulkServer');if(bulk){const selected=bulk.value;bulk.innerHTML='<option value="">请选择服务端</option>'+arenaConfig.servers.map(s=>`<option value="${esc(s.id)}">${esc(s.name||s.host||s.id)}</option>`).join('');bulk.value=selected;}
}
async function refreshArena(){
 if(arenaConfig)return;
 if(!arenaLoading)arenaLoading=(async()=>{const d=await api('arena/config');arenaConfig=d.config;arenaConsoleLinks(d.config.servers);if(d.loginDefaults)globalThis.dtLoginDefaults=d.loginDefaults;arenaRender();if(typeof arenaScanDefaults==='function')arenaScanDefaults();for(const el of document.querySelectorAll('.arena-cache-path'))el.textContent=d.cacheDir;await pollArena();})().finally(()=>arenaLoading=null);
 return arenaLoading;
}
async function arenaSave(){
 if(!arenaConfig)throw Error('配置尚未加载');
 const c=arenaRead(),revision=JSON.stringify(c);
 const result=await api('arena/save',{config:c});
 if(JSON.stringify(arenaRead())===revision){arenaConfig=result.config;arenaDirty=false;arenaConsoleLinks(result.config.servers);}
 for(const role of arenaRoles)$(arenaPage(role)+'Notice').textContent=arenaDirty?'保存期间又有修改，请再次保存。':'已加密保存到本机。';
 if(arenaDirty)throw Error('配置有新的修改，请保存后再执行');
}
function arenaSelected(role){return $(arenaPage(role)+'Selected').value.split(/[\s,，;；]+/).filter(Boolean);}
async function arenaPreview(role){
 await arenaSave();const p=await api('arena/preview',{role,selected:arenaSelected(role)});
 const box=$(arenaPage(role)+'Preview');box.hidden=false;
 box.textContent=`共 ${p.targets.length} 台目标\n`+p.targets.map(n=>`${n.host}:${n.port} · ${n.os} · ${n.user} → ${n.endpoint?`${n.endpoint.scheme}://${n.endpoint.host}:${n.endpoint.port}`:'待绑定服务端'}`).join('\n');
}
async function arenaRun(role,action){
 const choice=$(arenaPage(role)).querySelector('[data-arena-release-choice]').value;
 if(['cache','distribute','deploy'].includes(action)&&choice&&choice!==arenaConfig.releaseTag)throw Error('请先点击“应用所选版本”，再拉取或推送');
 await arenaSave();await api('arena/run',{role,action,selected:arenaSelected(role)});await pollArena();
}
async function arenaListReleases(){
 if(arenaReleaseListPending)return arenaReleaseListPending;
 for(const el of document.querySelectorAll('[data-release-list-status]'))el.textContent='正在扫描全部 Releases（包括后续分页）…';
 arenaReleaseListPending=(async()=>{
  const result=await api('arena/releases',{});arenaReleaseList=result.releases;
  for(const el of document.querySelectorAll('[data-arena-release-choice]')){
   const previous=el.value;
   el.innerHTML='<option value="">请选择指定版本，不会自动选择最新版</option>'+result.releases.map(r=>`<option value="${esc(r.tag)}" ${r.available?'':'disabled'}>${esc(r.tag)} · ${r.draft?'草稿':r.prerelease?'预发布':'正式版'} · ${r.assets.filter(a=>a.available).length}/4 安装包${r.available?'':' · 不可用'}</option>`).join('');
   el.value=result.releases.some(r=>r.available&&r.tag===previous)?previous:'';
  }
  for(const el of document.querySelectorAll('[data-release-list-status]'))el.textContent=`扫描完成：共 ${result.total} 个 Release，可用 ${result.releases.filter(r=>r.available).length} 个。选择版本并应用后，再拉取或推送。`;
  for(const el of document.querySelectorAll('[data-release-list-details]'))el.innerHTML='<summary>查看全部版本与安装包检查结果</summary><div class="tablewrap"><table><thead><tr><th>版本</th><th>类型</th><th>发布日期</th><th>产物检查</th></tr></thead><tbody>'+result.releases.map(r=>`<tr><td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.tag)}</a></td><td>${r.draft?'草稿':r.prerelease?'预发布':'正式版'}</td><td>${esc(r.publishedAt?.slice(0,10)||'—')}</td><td class="arena-release-reason">${esc(r.available?'四种安装包及 SHA256 齐全':r.reason)}</td></tr>`).join('')+'</tbody></table></div>';
 })().catch(e=>{for(const el of document.querySelectorAll('[data-release-list-status]'))el.textContent='扫描失败，未应用任何版本：'+e.message;throw e;}).finally(()=>arenaReleaseListPending=null);
 return arenaReleaseListPending;
}
async function arenaApplySelectedRelease(role){
 const tag=$(arenaPage(role)).querySelector('[data-arena-release-choice]').value;
 if(!tag)throw Error('请先扫描 Releases 并选择一个可用版本');
 await arenaApplyRelease(role,tag);
}
async function arenaApplyRelease(role,selectedTag){
 if(arenaReleaseApplying)throw Error('正在核对版本，请稍候');
 const input=$(arenaPage(role)).querySelector('[data-arena-release]'),before=input.value,value=selectedTag||before;
 $(arenaPage(role)+'Notice').textContent='正在核对 GitHub Release 的四种安装包和 SHA256…';
 arenaReleaseApplying=true;
 try{
 const result=await api('arena/release',{tag:value});
 if(input.value!==before||(selectedTag&&$(arenaPage(role)).querySelector('[data-arena-release-choice]').value!==selectedTag))throw Error('等待期间版本选择发生变化，本次结果未应用，请重新选择');
 arenaConfig=arenaRead();arenaConfig.releaseTag=result.tag;arenaConfig.artifacts=result.artifacts;arenaDirty=true;
 for(const el of document.querySelectorAll('[data-arena-release]'))el.value=result.tag;
 for(const el of document.querySelectorAll('[data-arena-release-choice]'))el.value=[...el.options].some(o=>o.value===result.tag)?result.tag:'';
 for(const el of document.querySelectorAll('[data-artifact]'))el.value=result.artifacts[el.dataset.artifact][el.dataset.field]||'';
 for(const r of arenaRoles)$(arenaPage(r)+'Notice').textContent=`已读取 ${result.tag} 的四个安装包与 SHA256；本地产物路径已清空，请保存配置。`;
 }catch(e){$(arenaPage(role)+'Notice').textContent='版本未应用：'+e.message;throw e;}finally{arenaReleaseApplying=false;}
}
async function pollArena(){
 if(!arenaConfig)return;
 const state=await api('arena/state');arenaBusy=state.running;
 if(state.cached&&arenaCachedJob!==state.id){
  if(!arenaDirty)for(const [key,sha] of Object.entries(state.cached)){
   arenaConfig.artifacts[key].sha256=sha;
   document.querySelector(`[data-artifact="${key}"][data-field="sha256"]`).value=sha;
  }
  arenaCachedJob=state.id;
 }
 for(const role of arenaRoles){
  const page=$(arenaPage(role));
  for(const el of page.querySelectorAll('input,select,button'))if(!el.closest('#arenaScanPanel'))el.disabled=arenaBusy;
  $(arenaPage(role)+'State').textContent=state.status+(state.role?' · '+arenaTitle(state.role):'');
  $(arenaPage(role)+'Logs').textContent=state.logs.join('\n')||'尚未执行任务。';
  const trust=$(arenaPage(role)+'Trust');
  trust.hidden=!state.trust?.length||state.running;
  trust.querySelector('pre').textContent=(state.trust||[]).map(n=>`${n.host} · ${n.algorithm}\n${n.fingerprint}`).join('\n\n');
  trust.querySelector('button').dataset.job=state.id||'';
 }
}
function arenaMount(){
 const nav=document.querySelector('.sidebar nav');
 nav.prepend(nav.querySelector('[data-tab="localDeploy"]'));
 for(const role of arenaRoles){
  const id=arenaPage(role),title=arenaTitle(role);
  labels[id]=[title,role==='server'?'部署并启动服务端，打开对应管理控制台。':'进入场控控制台完成客户端分发、登记与上线核对。'];
  const button=document.createElement('button');button.className='nav';button.dataset.tab=id;button.textContent=title;
  button.onclick=()=>{tab(id);guard(refreshArena);};nav.insertBefore(button,nav.querySelector('[data-tab="sshModule"]'));
  const page=document.createElement('section');page.id=id;page.className='tab';page.hidden=true;
  page.innerHTML=`<section class="panel"><div class="sectionhead"><h2>${title}目标</h2><button data-add>＋ ${role==='server'?'添加服务端':'添加 IP 段'}</button></div>`+
   (role==='server'?'<label>运行 Ansible 的本地 WSL 发行版<input id="arenaDistro" placeholder="Ubuntu-24.04"></label>':'<p class="hint">每个 IP 段可使用独立登录凭据并绑定不同服务端。结束地址的前三段随起始地址自动填充。</p>')+
   `${role==='client'?'<div class="actions spaced"><label>统一绑定待配置机位<select id="arenaBulkServer"><option value="">请选择服务端</option></select></label><button data-bind-unassigned class="secondary">绑定全部待配置机位</button></div><p class="hint">可先保存未绑定的机位清单，部署前必须选择服务端。</p>':''}<div id="${id}Rows"></div></section><section class="panel"><h2>版本选择与本地安装包缓存</h2><div class="actions spaced"><button data-action="releases" class="secondary">扫描全部 Releases</button></div><label>选择指定 Release<select data-arena-release-choice><option value="">先扫描 Releases</option></select></label><div class="actions spaced"><button data-action="selectedRelease">应用所选版本</button></div><p data-release-list-status class="hint">扫描只读取版本信息，不改变当前配置。</p><details data-release-list-details><summary>版本检查结果</summary></details><label>CSGArena 版本号 / Release 链接<input data-arena-release placeholder="v1.16.2 或 GitHub Release 链接"></label><div class="actions spaced"><button data-action="release" class="secondary">读取并应用到四种产物</button><a href="https://github.com/SZTU-ACM/CSGArena/releases" target="_blank" rel="noopener">打开官方 Releases</a></div><p class="hint">默认使用 SZTU-ACM/CSGArena v1.16.2。读取 Release 会替换四种产物地址和校验值、清空本地路径。需登录时复用本机 GitHub CLI（gh auth login），令牌不存入工作台。</p><p class="hint">先下载／复制到本机，完整校验后再通过 Ansible 内网分发。网络出口默认有线优先，每次任务重新识别本机地址；实际接口与回退情况显示在任务日志。已预置官方版本的地址和 SHA256，也可使用自定义文件。接受 Linux ELF / Windows EXE 单文件 x64 产物，暂不解包 ZIP。填写本地路径时优先使用本地文件。</p><p class="arena-cache-path hint"></p>`+
   ['linux','windows'].map(os=>{const key=role+'-'+os;return `<details><summary>${os==='linux'?'Linux':'Windows'} ${title}产物</summary><div class="nodefields">${[['path','Windows 本地文件完整路径'],['url','HTTPS 下载地址'],['sha256','SHA256（下载必填，本地文件可自动计算）']].map(([field,label])=>`<label>${label}<input data-artifact="${key}" data-field="${field}" autocomplete="off"></label>`).join('')}</div></details>`;}).join('')+
   `<div class="actions spaced"><button data-action="save" class="secondary">保存配置</button><button data-action="check" class="secondary">检查 Ansible 环境</button><button data-action="prepare" class="secondary">准备本地 Ansible（需外网）</button><button data-action="preview" class="secondary">预览目标清单</button><button data-action="cache" class="secondary">拉取本页安装包到本地</button><button data-action="distribute" class="secondary">仅推送所选版本</button><button data-action="deploy">一键部署并${role==='client'?'登记':'启动'}</button><button data-action="status" class="secondary">查询开启状态</button></div><p id="${id}Notice" role="status"></p><pre id="${id}Preview" hidden></pre><p class="hint">拉取会缓存本页 Linux / Windows 两种安装包，无需先添加目标；仅推送会复制到本次筛选目标的暂存目录，不启动或替换正在运行的客户端。部署会安装启动并登记，作用于本次筛选目标；IP 筛选留空时才操作本页全部目标。已存在的选手身份会保留；更换服务端需走管控程序维护流程。控制端需安装 Ansible、paramiko、pywinrm 和 ansible.windows，SSH 主机密钥／WinRM 证书需已受信任；具体准备步骤见 docs/arena-deployment.md。部署期间请保持工具运行。</p></section><section class="panel"><h2>管控任务 <span id="${id}State" class="pill">尚未执行</span></h2><pre id="${id}Logs"></pre></section>`;
  $('main').insertBefore(page,$('taskLogPanel'));
  const selection=document.createElement('label');selection.textContent='本次目标 IP（可选）';
  const filter=document.createElement('input');filter.id=id+'Selected';filter.placeholder='留空操作全部；多个 IP 用空格或逗号分隔';
  selection.append(filter);const hint=document.createElement('small');hint.textContent='仅从已保存清单中选择；不会删除或修改其他机位。可先预览本次目标。';selection.append(hint);
  page.querySelector('[data-action="save"]').parentElement.before(selection);
  const trust=document.createElement('section');trust.id=id+'Trust';trust.className='panel';trust.hidden=true;
  trust.innerHTML='<h2>首次 SSH 主机指纹确认</h2><p>下列地址尚无信任记录。请核对目标机器及 SHA256 指纹；确认后仅登记本次显示的公钥并继续原任务。已登记的指纹发生变化时会拒绝连接。</p><pre></pre><button data-action="trust">确认首次指纹并继续</button>';
  page.append(trust);
  arenaHandoff(page,role);
  page.addEventListener('input',e=>{
   if(e.target.closest('#arenaScanPanel'))return;
   if(e.target.hasAttribute('data-arena-release-choice'))return;
   arenaDirty=true;$(id+'Notice').textContent='有未保存修改';
   if(e.target.hasAttribute('data-arena-release'))for(const el of document.querySelectorAll('[data-arena-release]'))if(el!==e.target)el.value=e.target.value;
   if(e.target.hasAttribute('data-octet'))e.target.closest('.arena-row').querySelector(`[data-mirror="${e.target.dataset.octet}"]`).value=e.target.value;
   if(['host','name'].includes(e.target.dataset.arena)&&role==='server')arenaBindOptions();
  });
  page.addEventListener('change',e=>{
   if(e.target.closest('#arenaScanPanel'))return;
   if(e.target.hasAttribute('data-arena-release-choice')){for(const el of document.querySelectorAll('[data-arena-release-choice]'))el.value=e.target.value;return;}
   arenaDirty=true;
   if(e.target.dataset.arena==='privateKeyPath'){const row=e.target.closest('.arena-row');if(row.querySelector('[data-arena="os"]').value==='windows')row.querySelector('[data-arena="port"]').value='';}
   if(e.target.dataset.arena==='os'){
    const row=e.target.closest('.arena-row');row.querySelector('[data-linux-fields]').hidden=e.target.value==='windows';row.querySelector('[data-arena="port"]').value='';
   }
  });
  page.addEventListener('click',e=>guard(async()=>{
   if(e.target.closest('#arenaScanPanel,.arena-handoff'))return;
   const b=e.target.closest('button');if(!b)return;
   await refreshArena();if(arenaBusy)throw Error('请等待当前管控任务结束');
   if(b.hasAttribute('data-bind-unassigned')){const server=$('arenaBulkServer').value;if(!server)throw Error('请选择服务端');arenaConfig=arenaRead();for(const n of arenaConfig.clients)if(!n.server)n.server=server;arenaDirty=true;arenaRender();$(id+'Notice').textContent='已填写未绑定机位的服务端，请保存配置';}
   else if(b.hasAttribute('data-add')){
    arenaConfig=arenaRead();const n={id:crypto.randomUUID(),name:'',os:'linux',port:'',user:'root',password:'',privateKeyPath:'',keyPassphrase:'',...(globalThis.dtLoginDefaults||{}),rootPassword:'',elevation:'sudo'};
    Object.assign(n,role==='server'?{host:'',servicePort:39721,scheme:'https'}:{prefix:[192,168,1],start:1,end:10,server:arenaConfig.servers[0]?.id||''});
    arenaConfig[role+'s'].push(n);arenaDirty=true;arenaRender();
   } else if(b.dataset.remove){
    arenaConfig=arenaRead();
    if(role==='server'&&arenaConfig.clients.some(n=>n.server===b.dataset.remove))throw Error('此服务端仍被 IP 段引用，请先修改绑定或移除对应 IP 段');
    arenaConfig[role+'s']=arenaConfig[role+'s'].filter(n=>n.id!==b.dataset.remove);arenaDirty=true;arenaRender();
   } else if(b.dataset.action==='releases')await arenaListReleases();
   else if(b.dataset.action==='selectedRelease')await arenaApplySelectedRelease(role);
   else if(b.dataset.action==='release')await arenaApplyRelease(role);
   else if(b.dataset.action==='save')await arenaSave();
   else if(b.dataset.action==='preview')await arenaPreview(role);
   else if(b.dataset.action==='trust'){await api('arena/trust',{id:b.dataset.job});await pollArena();}
   else if(b.dataset.action)await arenaRun(role,b.dataset.action);
  }));
 }
 setInterval(()=>{if(arenaConfig)pollArena().catch(()=>{});},2000);
}
arenaMount();
