'use strict';
let arenaScanId=null,arenaScanRunning=false,arenaScanPending=null;
function arenaScanRanges(){return [...$('arenaScanRanges').querySelectorAll('.arena-scan-range')].map(row=>({start:row.querySelector('[data-start]').value.trim(),end:row.querySelector('[data-end]').value.trim()}));}
function arenaScanAddRange(start='192.168.1.1',end='192.168.1.254'){
 const row=document.createElement('div');row.className='arena-scan-range';
 row.innerHTML=`<label>起始 IPv4<input data-start value="${esc(start)}" placeholder="192.168.1.1"></label><span>—</span><label>结束 IPv4<input data-end value="${esc(end)}" placeholder="192.168.1.254"></label><button class="secondary danger" data-scan-remove>移除</button>`;
 $('arenaScanRanges').append(row);
}
function arenaScanServers(){return [...$('arenaServerRows').querySelectorAll('.arena-row')].map(row=>({id:row.dataset.id,name:row.querySelector('[data-arena="name"]').value,host:row.querySelector('[data-arena="host"]').value}));}
function arenaScanBindings(){
 const servers=arenaScanServers();
 for(const el of $('arenaScanPanel').querySelectorAll('[data-scan-server]')){
  const value=el.value,global=el.id==='arenaScanServer';
  el.innerHTML=`<option value="">${global?'请选择登记服务端':'使用统一服务端'}</option>`+servers.map(s=>`<option value="${esc(s.id)}">${esc(s.name||s.host||s.id)} · ${esc(s.host)}</option>`).join('');
  el.value=servers.some(s=>s.id===value)?value:global&&servers.length===1?servers[0].id:'';
 }
}
function arenaScanDefaults(){const d=globalThis.dtLoginDefaults;if(d&&!$('arenaScanKeyPath').value)$('arenaScanKeyPath').value=d.privateKeyPath||'';}
function arenaScanBulk(){return {user:$('arenaScanUser').value.trim(),password:$('arenaScanPassword').value,privateKeyPath:$('arenaScanKeyPath').value.trim(),keyPassphrase:$('arenaScanKeyPassphrase').value,rootPassword:$('arenaScanSeparateSudo').checked?$('arenaScanSudo').value:'',server:$('arenaScanServer').value,os:$('arenaScanOS').value};}
function arenaScanRows(){return [...$('arenaScanResults').querySelectorAll('tr')];}
function arenaScanCredentials(row){
 const bulk=arenaScanBulk(),value=key=>row.querySelector(`[data-seat="${key}"]`).value;
 return {user:value('user').trim()||bulk.user,password:value('password')||bulk.password,privateKeyPath:value('privateKeyPath').trim()||bulk.privateKeyPath,keyPassphrase:value('keyPassphrase')||bulk.keyPassphrase,rootPassword:value('rootPassword')||bulk.rootPassword,server:value('server')||bulk.server,os:value('os'),port:Number(value('port')),elevation:'sudo'};
}
function arenaScanPort(os,keyPath){return Number(os==='windows'&&!keyPath?$('arenaScanWinrm').value||5986:$('arenaScanSSH').value||22);}
function arenaScanAppend(result){
 const row=document.createElement('tr');row.dataset.ip=result.ip;
 const os=$('arenaScanOS').value||result.suggestedOS,port=arenaScanPort(os,$('arenaScanKeyPath').value.trim());
 row.innerHTML=`<td><input type="checkbox" data-seat-pick aria-label="选择 ${esc(result.ip)}" ${result.ports.includes(port)?'checked':''}></td><td><strong>${esc(result.ip)}</strong><small>${esc(result.evidence)}；开放 ${result.ports.map(esc).join(' / ')||'无管理端口'}</small></td>`+
  `<td><select data-seat="os" aria-label="${esc(result.ip)} 系统"><option value="linux" ${os==='linux'?'selected':''}>Linux</option><option value="windows" ${os==='windows'?'selected':''}>Windows</option></select><input data-seat="port" type="number" min="1" max="65535" value="${port}" aria-label="${esc(result.ip)} 登录端口"></td>`+
  `<td><input data-seat="user" placeholder="使用统一账号" autocomplete="off" aria-label="${esc(result.ip)} 账号"></td><td><input data-seat="password" type="password" placeholder="使用统一密码" autocomplete="new-password" aria-label="${esc(result.ip)} 密码"></td>`+
  `<td><input data-seat="privateKeyPath" placeholder="使用统一私钥路径" aria-label="${esc(result.ip)} 私钥路径"><input data-seat="keyPassphrase" type="password" placeholder="使用统一解锁口令" autocomplete="new-password" aria-label="${esc(result.ip)} 私钥解锁口令"></td><td><input data-seat="rootPassword" type="password" placeholder="默认同登录密码" autocomplete="new-password" aria-label="${esc(result.ip)} sudo 密码"></td><td><select data-seat="server" data-scan-server aria-label="${esc(result.ip)} 登记服务端"></select></td>`;
 $('arenaScanResults').append(row);arenaScanBindings();
}
function arenaScanRender(state){
 arenaScanRunning=state.running;
 if(state.id!==arenaScanId){arenaScanId=state.id;$('arenaScanResults').replaceChildren();}
 const existing=new Set(arenaScanRows().map(row=>row.dataset.ip));
 for(const result of state.found)if(!existing.has(result.ip))arenaScanAppend(result);
 $('arenaScanState').textContent=`${state.status} · 已检测 ${state.completed}/${state.total} · 在线响应 ${state.found.length} 台${state.errors?' · 探测异常 '+state.errors+' 台':''}`;
 $('arenaScanNetwork').textContent=(state.network||[]).join('；');
 $('arenaScanEmpty').hidden=state.found.length>0;$('arenaScanStop').disabled=!state.running;
}
function pollArenaScan(){
 if(!arenaConfig)return Promise.resolve();
 if(!arenaScanPending)arenaScanPending=api('arena/scan/state').then(arenaScanRender).finally(()=>arenaScanPending=null);
 return arenaScanPending;
}
async function arenaScanStart(){
 await refreshArena();
 await api('arena/scan/start',{ranges:arenaScanRanges(),sshPort:$('arenaScanSSH').value,winrmPort:$('arenaScanWinrm').value});
 $('arenaScanNotice').textContent='正在探测网络响应，不尝试登录或安装。';await pollArenaScan();
}
function arenaScanApplyBulk(){
 const bulk=arenaScanBulk();
 for(const row of arenaScanRows().filter(row=>row.querySelector('[data-seat-pick]').checked)){
  for(const key of ['user','password','privateKeyPath','keyPassphrase','rootPassword','server'])row.querySelector(`[data-seat="${key}"]`).value=bulk[key];
  if(bulk.os)row.querySelector('[data-seat="os"]').value=bulk.os;
  if(bulk.os||bulk.privateKeyPath)row.querySelector('[data-seat="port"]').value=arenaScanPort(row.querySelector('[data-seat="os"]').value,bulk.privateKeyPath);
 }
 $('arenaScanNotice').textContent='统一账号与密码已应用到勾选 IP；可以继续逐台修改。';
}
function arenaScanImport(){
 const picked=arenaScanRows().filter(row=>row.querySelector('[data-seat-pick]').checked);
 if(!picked.length)throw Error('请勾选要登记的 IP');
 const c=arenaRead(),serverIDs=new Set(c.servers.map(s=>s.id)),occupied=new Set(c.servers.map(s=>s.host));
 for(const n of c.clients)for(let i=n.start;i<=n.end;i++)occupied.add([...n.prefix,i].join('.'));
 const additions=[];let skipped=0;
 for(const row of picked){
  const ip=row.dataset.ip;if(occupied.has(ip)){skipped++;continue;}
  const credentials=arenaScanCredentials(row);
  if(!credentials.user)throw Error(ip+'：请填写统一账号或单机账号');
  if(credentials.server&&!serverIDs.has(credentials.server))throw Error(ip+'：请选择有效的登记服务端');
  if(!Number.isInteger(credentials.port)||credentials.port<1||credentials.port>65535)throw Error(ip+'：登录端口无效');
  const parts=ip.split('.').map(Number);
  additions.push({id:crypto.randomUUID(),name:ip,prefix:parts.slice(0,3),start:parts[3],end:parts[3],...credentials});occupied.add(ip);
 }
 c.clients.push(...additions);arenaConfig=c;arenaDirty=true;arenaRender();
 $('arenaScanNotice').textContent=`已加入 ${additions.length} 台选手端，跳过 ${skipped} 个已有 IP；尚未安装。核对后点击下方“一键部署并登记”。`;
 $('arenaClientNotice').textContent='目标清单有未保存修改；部署时会先保存并校验配置。';
}
function arenaScanMount(){
 const panel=document.createElement('section');panel.id='arenaScanPanel';panel.className='panel';
 panel.innerHTML='<h2>先扫描选手机位</h2><p class="hint">填写起始与结束 IPv4，可添加多个范围；每次最多 4096 个地址，尾号使用 1–254。扫描和推送默认有线优先，自动读取当前网卡和路由，不固定本机 IP；更具体的目标路由优先。</p><div id="arenaScanRanges"></div><div class="actions spaced"><button id="arenaScanAddRange" class="secondary">＋ 扫描范围</button></div><div class="nodefields"><label>SSH 探测端口<input id="arenaScanSSH" type="number" value="22" min="1" max="65535"></label><label>WinRM HTTPS 探测端口<input id="arenaScanWinrm" type="number" value="5986" min="1" max="65535"></label></div><div class="actions spaced"><button id="arenaScanStart">扫描在线机器</button><button id="arenaScanStop" class="secondary" disabled>停止扫描</button></div><p id="arenaScanNetwork" class="hint" role="status"></p><p id="arenaScanState" role="status">尚未扫描</p><p class="hint">TCP 可连接、拒绝响应或 ICMP 回应均视为有在线响应；未响应不等于关机。系统类型仅按管理端口建议，请核对。默认只勾选管理端口可连接的机器。</p><h3>统一登录与登记设置</h3><div class="nodefields"><label>统一登录账号<input id="arenaScanUser" value="root" autocomplete="off"></label><label>统一登录密码<input id="arenaScanPassword" type="password" autocomplete="new-password"></label><label>统一私钥绝对路径<input id="arenaScanKeyPath" placeholder="填写后仅使用此私钥登录" autocomplete="off"></label><label>统一私钥解锁口令<input id="arenaScanKeyPassphrase" type="password" autocomplete="new-password"></label><label>系统类型<select id="arenaScanOS"><option value="">按管理端口建议</option><option value="linux">Linux</option><option value="windows">Windows</option></select></label><label>自动登记服务端<select id="arenaScanServer" data-scan-server></select></label><label class="arena-sudo-toggle"><input id="arenaScanSeparateSudo" type="checkbox">另外设置统一 sudo 密码</label><label id="arenaScanSudoLabel" hidden>统一 sudo 密码<input id="arenaScanSudo" type="password" autocomplete="new-password" placeholder="留空仍使用各机登录密码"></label></div><p class="hint">默认 sudo 密码就是各机登录密码。单机字段留空时使用统一设置，填写后覆盖；Windows 使用管理员账号，无需 sudo。</p><div class="actions spaced"><button id="arenaScanApply" class="secondary">将统一设置应用到勾选 IP</button><button id="arenaScanSelectAll" class="secondary">全选</button><button id="arenaScanSelectNone" class="secondary">全不选</button><button id="arenaScanImport">加入选手端部署清单</button></div><p id="arenaScanEmpty" class="empty">扫描后在这里选择机位并登记凭据。</p><div class="tablewrap"><table class="arena-scan-table"><thead><tr><th>选择</th><th>IP / 探测结果</th><th>系统 / 端口</th><th>账号</th><th>密码</th><th>私钥路径 / 解锁口令</th><th>独立 sudo 密码</th><th>登记服务端</th></tr></thead><tbody id="arenaScanResults"></tbody></table></div><p id="arenaScanNotice" role="status"></p>';
 $('arenaDirectClient').querySelector('summary').after(panel);arenaScanAddRange();
 $('arenaScanAddRange').onclick=()=>arenaScanAddRange();$('arenaScanStart').onclick=()=>guard(arenaScanStart);
 $('arenaScanStop').onclick=()=>guard(async()=>{await api('arena/scan/cancel',{});await pollArenaScan();});
 $('arenaScanSeparateSudo').onchange=()=>{$('arenaScanSudoLabel').hidden=!$('arenaScanSeparateSudo').checked;};
 $('arenaScanKeyPath').onchange=()=>{for(const row of arenaScanRows()){const n=arenaScanCredentials(row);if(n.os==='windows'&&[Number($('arenaScanSSH').value||22),Number($('arenaScanWinrm').value||5986)].includes(n.port))row.querySelector('[data-seat="port"]').value=arenaScanPort(n.os,n.privateKeyPath);}};
 $('arenaScanApply').onclick=()=>guard(arenaScanApplyBulk);$('arenaScanImport').onclick=()=>guard(arenaScanImport);
 for(const [id,checked] of [['arenaScanSelectAll',true],['arenaScanSelectNone',false]])$(id).onclick=()=>{for(const row of arenaScanRows())row.querySelector('[data-seat-pick]').checked=checked;};
 panel.addEventListener('click',e=>{if(e.target.hasAttribute('data-scan-remove'))e.target.closest('.arena-scan-range').remove();});
 panel.addEventListener('change',e=>{if(['os','privateKeyPath'].includes(e.target.dataset.seat)){const row=e.target.closest('tr'),n=arenaScanCredentials(row);row.querySelector('[data-seat="port"]').value=arenaScanPort(n.os,n.privateKeyPath);}});
 setInterval(()=>pollArenaScan().catch(()=>{}),1500);
}
arenaScanMount();
