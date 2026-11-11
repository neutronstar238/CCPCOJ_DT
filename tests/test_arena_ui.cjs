const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {JSDOM}=require('jsdom');

function fixture(scan=false){
 const dom=new JSDOM('<aside class="sidebar"><nav><button data-tab="sshModule"></button><button data-tab="localDeploy"></button></nav></aside><main id="main"><section id="taskLogPanel"></section></main>',{url:'http://127.0.0.1:8000',runScripts:'outside-only'});
 const w=dom.window,calls=[];w.structuredClone=structuredClone;w.setInterval=()=>0;
 const config={distro:'Ubuntu-24.04',servers:[{id:'s1',name:'主控',host:'192.0.2.10',os:'linux',user:'admin',password:'login',rootPassword:'rootpass',port:22,servicePort:39721,scheme:'https',elevation:'sudo'}],clients:[{id:'r1',name:'机房',prefix:[192,0,2],start:20,end:22,os:'linux',port:22,user:'student',password:'secret',rootPassword:'',elevation:'sudo',server:'s1'}],artifacts:{}};
 w.api=async(path,data)=>{calls.push({path,data});if(path==='arena/config')return {config:structuredClone(config),cacheDir:'C:/cache'};if(path==='arena/save')return {config:data.config};if(path==='arena/state')return {running:false,status:'等待',logs:[]};if(path==='arena/preview')return {targets:[]};return {};};
 w.errors=[];w.guard=async fn=>{try{return await fn();}catch(e){w.errors.push(e.message);}};
 vm.runInContext('const $=id=>document.getElementById(id);const labels={};const esc=s=>String(s??"").replace(/[&<>"\x27]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\\\"":"&quot;","\x27":"&#39;"}[c]));function tab(){}',dom.getInternalVMContext());
 vm.runInContext(fs.readFileSync(require.resolve('../web/arena.js'),'utf8'),dom.getInternalVMContext());
 if(scan)vm.runInContext(fs.readFileSync(require.resolve('../web/arena-scan.js'),'utf8'),dom.getInternalVMContext());
 return {w,calls,close:()=>dom.window.close()};
}

test('separate pages and local deployment is first; editing prefix updates only its range',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();const d=f.w.document;
  assert.equal(d.querySelector('.sidebar nav').firstElementChild.dataset.tab,'localDeploy');
  assert.ok(d.getElementById('arenaServer'));assert.ok(d.getElementById('arenaClient'));
  const octet=d.querySelector('[data-octet="1"]');octet.value='16';octet.dispatchEvent(new f.w.Event('input',{bubbles:true}));
  assert.equal(d.querySelector('[data-mirror="1"]').value,'16');assert.equal(d.querySelector('[data-mirror="1"]').readOnly,true);
  assert.deepEqual(Array.from(f.w.arenaRead().clients[0].prefix),[192,16,2]);
  assert.equal(f.w.arenaRead().servers[0].host,'192.0.2.10');
 }finally{f.close();}
});

test('client handoff opens the saved HTTPS console without running DT tasks; direct tools are opt-in',async()=>{
 const f=fixture(true);try{
  await f.w.refreshArena();const d=f.w.document,page=d.getElementById('arenaClient');
  const link=page.querySelector('[data-arena-consoles] a');
  assert.equal(link.href,'https://192.0.2.10:39721/');
  assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');
  const legacy=d.getElementById('arenaDirectClient');
  assert.equal(legacy.open,false);
  assert.ok(legacy.contains(d.getElementById('arenaScanPanel')));
  assert.ok(legacy.contains(page.querySelector('[data-action="deploy"]')));
  assert.ok(!legacy.contains(link));
  d.querySelector('#arenaServerRows [data-arena="servicePort"]').value='4443';
  assert.equal(link.port,'39721');await f.w.arenaSave();
  assert.equal(page.querySelector('[data-arena-consoles] a').port,'4443');
  assert.ok(!f.calls.some(c=>c.path==='arena/run'));
 }finally{f.close();}
});

test('deploy saves current input first; failed save never dispatches deployment',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();f.calls.length=0;
  f.w.document.querySelector('#arenaClientRows [data-arena="end"]').value='25';
  await f.w.arenaRun('client','deploy');
  assert.equal(f.calls[0].path,'arena/save');assert.equal(f.calls[0].data.config.clients[0].end,25);
  assert.equal(f.calls[1].path,'arena/run');assert.equal(f.calls[1].data.role,'client');
  f.calls.length=0;f.w.api=async(path)=>{f.calls.push({path});throw Error('重叠 IP');};
  await assert.rejects(f.w.arenaRun('client','deploy'),/重叠/);assert.equal(f.calls.length,1);
 }finally{f.close();}
});

test('changing OS clears old transport port and hides Linux elevation fields',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();const row=f.w.document.querySelector('#arenaClientRows .arena-row'),os=row.querySelector('[data-arena="os"]');
  os.value='windows';os.dispatchEvent(new f.w.Event('change',{bubbles:true}));
  assert.equal(row.querySelector('[data-arena="port"]').value,'');assert.equal(row.querySelector('[data-linux-fields]').hidden,true);
 }finally{f.close();}
});

test('referenced server removal is blocked and credentials stay out of browser storage',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();f.w.document.querySelector('#arenaServerRows [data-remove]').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(f.w.errors[0],/仍被 IP 段引用/);assert.equal(f.w.arenaRead().servers.length,1);
  assert.equal(f.w.localStorage.length,0);assert.equal(f.w.sessionStorage.length,0);
 }finally{f.close();}
});

test('applying a verified release updates four sources and preserves target credentials',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();const artifacts={};
  for(const role of ['server','client'])for(const os of ['linux','windows'])artifacts[role+'-'+os]={path:'',url:`https://github.com/SZTU-ACM/CSGArena/releases/download/v1.12.0/csgarena_${role}_${os}_amd64_v1.12.0${os==='windows'?'.exe':''}`,sha256:'a'.repeat(64)};
  f.w.api=async()=>({tag:'v1.12.0',artifacts});
  await f.w.arenaApplyRelease('client');
  const c=f.w.arenaRead();assert.equal(c.releaseTag,'v1.12.0');assert.equal(c.servers[0].password,'login');assert.equal(c.clients[0].server,'s1');
  assert.equal(c.artifacts['client-windows'].url,artifacts['client-windows'].url);
  f.w.api=async()=>{throw Error('missing asset');};
  await assert.rejects(f.w.arenaApplyRelease('server'),/missing asset/);
  assert.equal(f.w.arenaRead().artifacts['client-windows'].url,artifacts['client-windows'].url);
 }finally{f.close();}
});

test('scan import uses shared credentials, per-IP overrides and skips existing IPs without filling gaps',async()=>{
 const f=fixture(true);try{
  await f.w.refreshArena();const d=f.w.document;
  f.w.arenaScanRender({id:'scan1',running:false,status:'完成',completed:4,total:4,found:[21,23,25,26].map(n=>({ip:'192.0.2.'+n,ports:n===26?[]:[22],suggestedOS:'linux',evidence:'响应'}))});
  d.getElementById('arenaScanUser').value='contest';d.getElementById('arenaScanPassword').value='shared-password';
  const row=d.querySelector('[data-ip="192.0.2.25"]');row.querySelector('[data-seat="password"]').value='individual-password';
  f.w.arenaScanImport();let c=f.w.arenaRead();
  assert.equal(c.clients.length,3);assert.equal(c.clients[1].start,23);assert.equal(c.clients[2].start,25);
  assert.equal(c.clients[1].password,'shared-password');assert.equal(c.clients[1].rootPassword,'');
  assert.equal(c.clients[2].password,'individual-password');assert.equal(c.clients[2].rootPassword,'');
  assert.equal(c.clients[2].server,'s1');assert.equal(f.calls.some(x=>x.path==='arena/run'),false);
  f.w.arenaScanImport();assert.equal(f.w.arenaRead().clients.length,3);
  assert.equal(f.w.localStorage.length,0);
 }finally{f.close();}
});

test('scan rows keep entered credentials across polls and support separate sudo override',async()=>{
 const f=fixture(true);try{
  await f.w.refreshArena();const d=f.w.document,state={id:'scan1',running:false,status:'完成',completed:1,total:1,found:[{ip:'192.0.2.30',ports:[22],suggestedOS:'linux',evidence:'响应'}]};
  f.w.arenaScanRender(state);d.getElementById('arenaScanUser').value='contest';d.getElementById('arenaScanPassword').value='login';
  d.getElementById('arenaScanSeparateSudo').checked=true;d.getElementById('arenaScanSudo').value='sudo-password';
  f.w.arenaScanApplyBulk();f.w.arenaScanRender(state);
  const row=d.querySelector('[data-ip="192.0.2.30"]');assert.equal(row.querySelector('[data-seat="rootPassword"]').value,'sudo-password');
  row.querySelector('[data-seat="rootPassword"]').value='specific-sudo';f.w.arenaScanImport();
  assert.equal(f.w.arenaRead().clients.at(-1).rootPassword,'specific-sudo');
 }finally{f.close();}
});

test('scan request sends only ranges and ports, never the entered login credentials',async()=>{
 const f=fixture(true);try{
  await f.w.refreshArena();f.w.document.getElementById('arenaScanPassword').value='secret-credential';
  f.w.api=async(path,data)=>{f.calls.push({path,data});return path==='arena/scan/state'?{id:'new',running:true,status:'扫描中',completed:0,total:254,found:[]}:{};};
  await f.w.arenaScanStart();const call=f.calls.find(x=>x.path==='arena/scan/start');
  assert.ok(call.data.ranges.length);assert.equal(JSON.stringify(call.data).includes('secret-credential'),false);
 }finally{f.close();}
});

test('private key defaults and Windows scan import select SSH while preserving unlock spaces',async()=>{
 const f=fixture(true);try{
  await f.w.refreshArena();const d=f.w.document;
  d.getElementById('arenaScanKeyPath').value='C:\\fixture\\key';
  d.getElementById('arenaScanKeyPassphrase').value=' unlock ';
  f.w.arenaScanRender({id:'key',running:false,status:'完成',completed:1,total:1,found:[{ip:'192.0.2.35',ports:[22,5986],suggestedOS:'windows',evidence:'响应'}]});
  const row=d.querySelector('[data-ip="192.0.2.35"]');assert.equal(row.querySelector('[data-seat=port]').value,'22');
  f.w.arenaScanImport();const n=f.w.arenaRead().clients.at(-1);
  assert.equal(n.privateKeyPath,'C:\\fixture\\key');assert.equal(n.keyPassphrase,' unlock ');assert.equal(n.user,'root');assert.equal(n.port,22);
 }finally{f.close();}
});

test('release discovery lists all versions without choosing latest and applies only explicit choice',async()=>{
 const f=fixture();try{
  await f.w.refreshArena();const d=f.w.document,artifacts={};
  for(const role of ['server','client'])for(const os of ['linux','windows'])artifacts[role+'-'+os]={path:'',url:'https://example.org/'+role+'-'+os,sha256:'b'.repeat(64)};
  const releases=[{tag:'v9.0.0',available:true,prerelease:true,draft:false,assets:[{available:true},{available:true},{available:true},{available:true}],url:'https://github.com/SZTU-ACM/CSGArena/releases/tag/v9.0.0'}, {tag:'v1.0.0',available:false,assets:[],reason:'缺少安装包',url:'https://github.com/SZTU-ACM/CSGArena/releases/tag/v1.0.0'}];
  f.w.api=async(path,data)=>{f.calls.push({path,data});if(path==='arena/releases')return {releases,total:2};if(path==='arena/release')return {tag:data.tag,artifacts};return {};};
  await f.w.arenaListReleases();
  assert.equal(d.querySelector('[data-arena-release-choice]').value,'');assert.equal(f.w.arenaRead().releaseTag,'v1.16.2');
  assert.equal(d.querySelector('option[value="v1.0.0"]').disabled,true);
  await assert.rejects(f.w.arenaApplySelectedRelease('server'),/选择一个/);
  const chooser=d.querySelector('#arenaServer [data-arena-release-choice]');chooser.value='v9.0.0';chooser.dispatchEvent(new f.w.Event('change',{bubbles:true}));
  assert.equal(d.querySelector('#arenaClient [data-arena-release-choice]').value,'v9.0.0');
  await assert.rejects(f.w.arenaRun('client','cache'),/应用所选版本/);
  await f.w.arenaApplySelectedRelease('client');assert.equal(f.w.arenaRead().releaseTag,'v9.0.0');
  assert.equal(f.calls.some(c=>c.path==='arena/run'),false);
 }finally{f.close();}
});
