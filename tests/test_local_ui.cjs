const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');

function fixture(){
 const fields=[{dataset:{local:'path'},type:'text',value:'/srv/old',disabled:false}];
 const elements=new Map();
 const $=id=>{if(!elements.has(id))elements.set(id,{checked:false,value:'',dataset:{},addEventListener(){},querySelectorAll(){return id==='localSettings'?fields:[];},textContent:''});return elements.get(id);};
 const calls=[],context=vm.createContext({$,console,setTimeout,clearTimeout,window:{addEventListener(){}},document:{addEventListener(){}},
  api:async(path,data)=>{calls.push({path,data});if(path==='local/save')return {config:{nodes:[]}};if(path==='local/state')return {running:false,nodes:{},logs:[],status:'完成'};return {};},guard:f=>f(),esc:String});
 vm.runInContext(fs.readFileSync(require.resolve('../web/local.js'),'utf8'),context);
 vm.runInContext("localData={config:{nodes:[]},configPath:'fixture.dpapi'}",context);
 return {fields,$,calls,run:s=>vm.runInContext(s,context)};
}
test('clicking inspect immediately after typing flushes the unsaved path first',async()=>{
 const f=fixture();f.fields[0].value='/srv/changed';f.run('scheduleLocalSave()');
 await f.run("localRun('syncVersions')");
 assert.equal(f.calls[0].path,'local/save');assert.equal(f.calls[0].data.settings.path,'/srv/changed');
 assert.equal(f.calls[1].path,'local/run');assert.equal(f.run('localDirty'),false);
});
test('local update requires explicit selection and confirmation',async()=>{
 const f=fixture();await assert.rejects(f.run("localRun('batchUpdate')"),/勾选本地/);
 f.run("localSelected.add('localweb')");await assert.rejects(f.run("localRun('batchUpdate')"),/确认/);
 assert.equal(f.calls.length,0);
 f.$('localConfirm').checked=true;await f.run("localRun('batchUpdate')");
 assert.equal(f.calls[0].data.confirmed,true);
});
test('editing settings invalidates an earlier maintenance confirmation',()=>{
 const f=fixture();f.$('localConfirm').checked=true;f.run('scheduleLocalSave()');
 assert.equal(f.$('localConfirm').checked,false);assert.equal(f.run('localDirty'),true);
 f.run('clearTimeout(localSaveTimer)');
});
test('close sends only explicitly selected service and needs no update confirmation',async()=>{
 const f=fixture();await assert.rejects(f.run("localRun('stop')"),/勾选本地/);
 f.run("localSelected.add('localpcj')");await f.$('localStop').onclick();
 assert.equal(f.calls[0].data.action,'stop');assert.equal(JSON.stringify(f.calls[0].data.selected),'["localpcj"]');
});
test('service status distinguishes stopped, running, partial, unknown and failed checks',()=>{
 const f=fixture();
 assert.equal(f.run("localServiceState({kind:'judge',pods:1,deployed:true},{containers:[{running:false}],runningPods:0}).text"),'已关闭 · 0/1 pods');
 assert.equal(f.run("localServiceState({kind:'judge',pods:1,deployed:true},{containers:[{running:true}],runningPods:1}).tone"),'running');
 assert.equal(f.run("localServiceState({kind:'judge',pods:2,deployed:true},{containers:[{running:true},{running:false}]}).tone"),'error');
 assert.equal(f.run("localServiceState({kind:'web',deployed:true},{}).text"),'未检测');
 assert.equal(f.run("localServiceState({kind:'judge',pods:1},{error:'unavailable',containers:[{running:true}]}).tone"),'error');
});
