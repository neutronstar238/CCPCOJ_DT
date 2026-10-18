// Local DOM regression tests. No SSH, Docker, registry or production API access.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');
const web=path.join(__dirname,'../web');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(t){
 const dom=new JSDOM(fs.readFileSync(path.join(web,'index.html'),'utf8'),{url:'http://127.0.0.1/',runScripts:'outside-only'});
 t.after(()=>{vm.runInContext('taskObserver.disconnect()',dom.getInternalVMContext());dom.window.close();});
 const w=dom.window,ctx=dom.getInternalVMContext(),routes={},calls=[];
 w.structuredClone=structuredClone;w.setInterval=()=>0;w.scrollTo=()=>{};
 w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 const c={nodes:[{id:'pcj01',kind:'judge',host:'127.0.0.1',user:'qa',sshPort:22,keyName:'pcj01',role:'active',path:'/tmp/qa',pods:2,web:'web01',targetVersion:'old'}],backups:[]};
 const state={running:false,job:null,status:'等待',logs:[],nodes:{},results:{}};
 const cache={cacheDir:'C:/qa/images',indexPath:'C:/qa/images/index.json',archives:[],selected:{},dependencies:[]};
 w.fetch=async(url,options={})=>{
  const name=url.replace('/api/',''),body=options.body?JSON.parse(options.body):undefined;calls.push({name,body});
  let result;
  if(routes[name])result=await routes[name](body);
  else if(name==='save')result={config:structuredClone(body.config),plan:[]};
  else if(name==='config')result={config:structuredClone(c)};
  else if(name.endsWith('state'))result=structuredClone(state);
  else if(name==='artifacts/cache')result=structuredClone(cache);
  else if(name==='ssh/profiles'||name==='ssh/save')result={profiles:body?.profiles||[]};
  else result={ok:true};
  return {ok:true,json:async()=>result};
 };
 for(const name of ['app','ssh','artifacts','batch','archive-delete','database','local','tasks'])vm.runInContext(fs.readFileSync(path.join(web,name+'.js'),'utf8'),ctx,{filename:name+'.js'});
 const run=code=>vm.runInContext(code,ctx);w.qaConfig=c;run('load(qaConfig,true)');
 return {w,$:id=>w.document.getElementById(id),run,routes,calls,c,state,cache};
}
test('WEB-01 one Finish intent survives blur autosave and waits for latest edit',async t=>{
 const f=fixture(t),d=deferred();let writes=0;
 f.routes.save=async body=>{writes++;if(writes===1)await d.promise;return {config:structuredClone(body.config),plan:[]};};
 f.run("editNode(0); document.querySelector('[data-key=host]').value='127.0.0.2'");
 const finish=f.$('nodeForm').querySelector('[type=submit]');finish.dispatchEvent(new f.w.Event('pointerdown'));
 f.w.document.querySelector('[data-key=host]').dispatchEvent(new f.w.Event('change',{bubbles:true}));await tick();
 assert.equal(f.$('nodeDialog').open,true);assert.equal(finish.disabled,false);
 f.$('nodeForm').dispatchEvent(new f.w.Event('submit',{cancelable:true}));d.resolve();for(let i=0;i<8;i++)await tick();
 assert.equal(f.$('nodeDialog').open,false);assert.equal(f.run('config.nodes[0].host'),'127.0.0.2');
});
test('WEB-02 checkbox selected during SSH autosave survives response',async t=>{
 const f=fixture(t),d=deferred();f.routes['ssh/save']=async body=>{await d.promise;return {profiles:body.profiles};};
 f.run("sshProfiles=[{keyName:'pcj01',host:'127.0.0.1',user:'qa'}];sshLoaded=true;renderSSH()");
 const p=f.run('saveSSH(true)');await tick();f.w.document.querySelector('.sshselect').checked=true;d.resolve();await p;
 assert.equal(f.w.document.querySelector('.sshselect').checked,true);
});
test('WEB-03 instant completed job refreshes metadata, retries a failed read',async t=>{
 const f=fixture(t);f.run('sshLoaded=true');let count=0;
 f.routes['ssh/state']=()=>({...f.state,job:'instant'});
 f.routes['ssh/profiles']=()=>{if(++count===1)throw Error('temporary read failure');return {profiles:[{keyName:'pcj01',exists:true,localPath:'qa',fingerprint:'SHA256:qa'}]};};
 await assert.rejects(f.run('pollSSH()'));await f.run('pollSSH()');
 assert.match(f.$('sshKeyPaths').textContent,/SHA256:qa/);assert.equal(count,2);
});
test('WEB-04 empty scan selection disables import and cannot submit',async t=>{
 const f=fixture(t);f.$('sshImportRows').innerHTML='<tr><td><input class="sshimport" type="checkbox" checked value="qa"></td></tr>';
 const input=f.w.document.querySelector('.sshimport');input.checked=false;input.dispatchEvent(new f.w.Event('change',{bubbles:true}));
 assert.equal(f.$('sshConfirmImport').disabled,true);await f.$('sshConfirmImport').onclick();
 assert.equal(f.calls.some(c=>c.name==='ssh/import'),false);
});
test('WEB-05 version change clears an archive from a different version',async t=>{
 const f=fixture(t);f.$('imagePath').value='C:/old.tar';f.$('cachedVersions').innerHTML='<option value="new">new</option>';
 f.$('cachedVersions').dispatchEvent(new f.w.Event('change',{bubbles:true}));await tick();
 assert.equal(f.$('imageVersion').value,'new');assert.equal(f.$('imagePath').value,'');
});
test('WEB-06 delayed save preserves new target, focus, caret and eventually persists it',async t=>{
 const f=fixture(t),d=deferred();let n=0;f.routes.save=async body=>{if(++n===1)await d.promise;return {config:structuredClone(body.config),plan:[]};};
 const p=f.run('save()');await tick();const input=f.w.document.querySelector('.batch-target');input.focus();input.value='new-version';input.setSelectionRange(3,5);input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 d.resolve();await p;
 assert.equal(f.run('config.nodes[0].targetVersion'),'new-version');assert.equal(f.w.document.activeElement.value,'new-version');assert.equal(f.w.document.activeElement.selectionStart,3);
 await f.run('saveBatchTargets()');assert.equal(f.calls.filter(c=>c.name==='save').at(-1).body.config.nodes[0].targetVersion,'new-version');
});
test('WEB-07 a task locks conflicting controls on all pages; cancel remains usable',async t=>{
 const f=fixture(t);f.routes['artifacts/run']=()=>({job:'slow'});await f.run("api('artifacts/run',{action:'pull'})");
 for(const id of ['addWeb','sshGenerate','localStart','imagePull','refreshPlan'])assert.equal(f.$(id).inert,true,id);
 assert.notEqual(f.$('stop').inert,true);assert.notEqual(f.$('localStopTask').inert,true);
 await assert.rejects(f.run("api('local/run',{action:'start'})"),/尚未完成/);
});
test('WEB-08 duplicate polls share one request and keep lock through cache refresh',async t=>{
 const f=fixture(t),d=deferred();let reads=0;
 f.routes['artifacts/state']=()=>{reads++;return {...f.state,job:'done',action:'import',result:{}};};
 f.routes['artifacts/cache']=async()=>{await d.promise;return f.cache;};
 const a=f.run('artifactPoll()');await tick();const b=f.run('artifactPoll()');
 assert.equal(reads,1);assert.equal(f.$('imagePull').inert,true);assert.match(f.$('artifactState').textContent,/应用/);
 d.resolve();await Promise.all([a,b]);assert.equal(f.$('imagePull').inert,false);assert.match(f.$('artifactMessage').textContent,/就绪/);
});
test('scan lists exact manifest versions without choosing a default target',t=>{
 const f=fixture(t);f.w.discovery={archives:[{path:'C:/false-name.tar',images:{'csgrandeur/ccpcoj-web2:actual':'sha256:a'},size:1}],docker:{images:[],message:'offline'},roots:['C:/qa'],rejected:[]};
 f.run('renderDiscoveredImages(discovery)');assert.equal(f.$('imageVersion').value,'');assert.match(f.$('imageDetected').textContent,/:actual/);assert.equal(f.$('imagePath').value,'');
});
test('explicit detected archive selection applies its real kind and version',async t=>{
 const f=fixture(t);f.w.discovery={archives:[{path:'C:/judge.tar',images:{'csgrandeur/ccpcoj-judge2:actual':'sha256:a'},size:1}],docker:{images:[],message:''},roots:[],rejected:[]};
 f.run('renderDiscoveredImages(discovery)');f.$('imageDetected').value='0';f.$('imageDetected').dispatchEvent(new f.w.Event('change'));await tick();
 assert.equal(f.$('imagePath').value,'C:/judge.tar');assert.equal(f.$('imageVersion').value,'actual');assert.equal(f.$('imageKind').value,'judge');
});
test('overlapping cache refreshes apply in order and the later refresh reads fresh data',async t=>{
 const f=fixture(t),d=deferred();let reads=0;
 f.routes['artifacts/cache']=async()=>{const n=++reads;if(n===1)await d.promise;return {...f.cache,cacheDir:'generation-'+n};};
 const a=f.run('refreshArtifactCache()');await tick();const b=f.run('refreshArtifactCache()');
 assert.equal(reads,1);d.resolve();await Promise.all([a,b]);assert.match(f.$('cacheLocation').textContent,/generation-2/);
});
