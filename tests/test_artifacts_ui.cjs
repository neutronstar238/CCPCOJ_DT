const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/artifacts.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function cachedImageReady('), source.indexOf('function renderImageInventory(')), context);
const nodes = [{id:'web01',kind:'web'}, {id:'pcj01',kind:'judge'}];
const web = 'csgrandeur/ccpcoj-web2:2.0.288-exp';
const judge = 'csgrandeur/ccpcoj-judge2:2.0.288-exp';
function cache(refs) {
  return {dependencies:['mysql:8.0.32'], selected:Object.fromEntries(refs.map(ref=>[ref,'file'])),
    archives:[{id:'file', available:true, images:Object.fromEntries(refs.map(ref=>[ref,'image']))}]};
}
function missing(c,ids) { return Array.from(context.missingDistributionImages(c,nodes,ids,'2.0.288-exp')); }
test('Web cache does not satisfy judge distribution',()=>assert.deepEqual(missing(cache([web]),['pcj01']),[judge]));
test('Judge distribution needs no Web database dependency',()=>assert.deepEqual(missing(cache([judge]),['pcj01']),[]));
test('Web lists missing database and mixed targets list judge too',()=>assert.deepEqual(missing(cache([web]),['web01','pcj01']),[judge,'mysql:8.0.32']));
test('Unavailable archive is missing even when selected',()=>{const c=cache([judge]);c.archives[0].available=false;assert.deepEqual(missing(c,['pcj01']),[judge]);});
test('Only explicitly selected archive satisfies requirement',()=>{const c=cache([judge]);c.selected[judge]='other';assert.deepEqual(missing(c,['pcj01']),[judge]);});

test('Opening console resets old dependency opt-in; explicit current choice survives refresh',()=>{
  const app=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
  const elements={}; const sandbox=vm.createContext({Set,config:{},selected:new Set(),$:id=>elements[id]||(elements[id]={}),renderNodes(){},showTool(){}});
  const start=app.indexOf('function load('); const end=app.indexOf('\n',start);
  vm.runInContext(app.slice(start,end),sandbox);
  sandbox.load({nodes:[],uiChoices:{imageDependencies:true}},true);
  assert.equal(elements.imageDependencies.checked,false);
  elements.imageDependencies.checked=true;
  sandbox.load({nodes:[],uiChoices:{}},false);
  assert.equal(elements.imageDependencies.checked,true);
});
