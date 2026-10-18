'use strict';
let artifactJob=null, artifactSeen=null, artifactBusy=false, releaseRows=[], artifactCache=null, releasesLoaded=false, distributionJob=null, distributionSavedNodes=[];
labels.artifacts=['版本与镜像分发','先在 Windows 准备资源，再经内网 SSH 分发。分发不启动或更新业务服务。'];
const artifactButtons=['releaseFetch','releaseDownload','tagFetch','imagePull','imageImport','imageUpload','imageDistribute','cacheRefresh','imageDetect','dockerExport'];
function artifactMessage(text){$('artifactMessage').textContent=text;}
async function artifactStart(action,data={}){
 if(artifactBusy||running)throw Error('资源任务或缓存列表仍在处理中，请稍候');
 $('resourceHistory').open=true;
 if(preferencesTimer)autoSaveSettings();await preferencesQueue;const r=await api('artifacts/run',{action,...data});artifactJob=r.job;artifactSeen=null;artifactMessage('任务已开始，进度见下方日志。');await artifactPoll();
}
async function artifactGuard(fn){try{await fn();}catch(e){artifactMessage(e.message);}}
function renderReleaseAssets(){
 const version=$('releaseVersion').value.trim(),r=releaseRows.find(x=>x.version===version),assets=r?.assets||[];
 $('releaseAsset').innerHTML=assets.length?assets.map(a=>`<option value="${esc(a.name)}">${esc(a.name)} · ${(a.size/1048576).toFixed(1)} MB</option>`).join(''):`<option value="">${!version?'请选择 Release 标签':!releasesLoaded?'尚未查询仓库':!r?'仓库未发布此 Release':'该 Release 没有 Linux amd64 附件'}</option>`;
 $('releaseHint').textContent=!version?'先查询再选择 Release，或上传本地程序；不会自动选择版本。':!releasesLoaded?'点击查询全部 Release 获取仓库附件。本地已选文件显示在下方。':!r?`仓库未发布 ${version||'所填标签'} 的 Release，不能从仓库下载。镜像标签不等于部署程序 Release；可改选已发布版本，或上传本地部署程序。`:!assets.length?'此 Release 没有 Linux amd64 部署附件，请上传本地程序。':`已找到 ${assets.length} 个 Linux amd64 附件。本地已选文件不受查询影响。`;
}
$('releaseVersion').oninput=$('releaseVersion').onchange=renderReleaseAssets;
$('releaseFetch').onclick=()=>artifactGuard(()=>artifactStart('releases'));
$('releaseDownload').onclick=()=>artifactGuard(()=>artifactStart('tool',{version:$('releaseVersion').value.trim(),asset:$('releaseAsset').value||undefined}));
$('toolFile').onchange=()=>artifactGuard(async()=>{
 const f=$('toolFile').files[0];if(!f)return;
 $('toolFile').disabled=true;
 try{config.tool=await api('tool',f,true);if(config.tool.version){$('toolVersion').value=config.tool.version;config.toolVersion=config.tool.version;}showTool();invalidatePlan();if(config.nodes.length)await save();artifactMessage('本地部署程序已选用并缓存。无需该版本存在于 Release 列表。');}
 finally{$('toolFile').disabled=false;$('toolFile').value='';}
});
$('tagFetch').onclick=()=>artifactGuard(()=>artifactStart('tags',{kind:$('imageKind').value==='both'?'web':$('imageKind').value}));
$('fetchVersions').onclick=()=>artifactGuard(async()=>{const n=config.nodes.find(n=>n.id===$('targetNode').value);if(!n)throw Error('先选择操作节点');$('imageKind').value=n.kind;tab('artifacts');await refreshArtifactCache();await artifactStart('tags',{kind:n.kind});});
$('imagePull').onclick=()=>artifactGuard(()=>artifactStart('pull',{kind:$('imageKind').value,version:$('imageVersion').value.trim(),dependencies:$('imageDependencies').checked,mode:$('imageSource').value,mirror:$('imageMirror').value.trim()}));
function showSourceOrder(){
 const mode=$('imageSource').value;
 $('imageMirrorField').hidden=mode!=='custom';
 $('sourceOrder').textContent='失败回退顺序：'+({tencent:'腾讯云 → Docker Hub',hub:'Docker Hub → 腾讯云',custom:'自定义源 → 腾讯云 → Docker Hub'}[mode])+'。逐个尝试，不修改 Docker 永久配置。';
}
$('imageSource').onchange=showSourceOrder;
$('imageImport').onclick=()=>artifactGuard(()=>artifactStart('import',{path:$('imagePath').value.trim()}));
$('imageUpload').onclick=()=>artifactGuard(()=>artifactStart('import'));
$('cachedVersions').onchange=()=>{$('imageVersion').value=$('cachedVersions').value;invalidatePlan();$('imageVersion').dispatchEvent(new Event('change',{bubbles:true}));};
$('cacheRefresh').onclick=()=>artifactGuard(refreshArtifactCache);
let artifactCacheRefresh=Promise.resolve();
function refreshArtifactCache(){artifactCacheRefresh=artifactCacheRefresh.catch(()=>{}).then(loadArtifactCache);return artifactCacheRefresh;}
async function loadArtifactCache(){
 const [cache,serverConfig]=await Promise.all([api('artifacts/cache'),api('config')]);
 artifactCache=cache;
 artifactCache.dependencies ||= JSON.parse($('imageInventory').dataset.dependencies);
 $('cacheLocation').textContent='下载 / 导入 / 刷新共用目录：'+artifactCache.cacheDir+'；索引：'+artifactCache.indexPath;
 const savedNodes=serverConfig.config.nodes;distributionSavedNodes=savedNodes;
 const previousVersion=$('cachedVersions').value;
 const refs=Object.keys(artifactCache.selected).filter(ref=>/^csgrandeur\/ccpcoj-(web|judge)2:/.test(ref));
 $('cachedVersions').innerHTML='<option value="">选择本地已有版本</option>'+refs.map(ref=>`<option value="${esc(ref.split(':').pop())}">${esc(ref)}</option>`).join('');
 if([...$('cachedVersions').options].some(o=>o.value===previousVersion))$('cachedVersions').value=previousVersion;
 $('archiveRows').innerHTML=artifactCache.archives.map(a=>`<tr><td><strong>${esc(a.name)}</strong><small class="artifact-path">${esc(a.path)}</small><small class="artifact-path">SHA256 ${esc(a.sha256)}</small></td><td>${Object.entries(a.images).map(([tag,id])=>`<div>${esc(tag)} ${artifactCache.selected[tag]===a.id?'<span class="pill">当前选用</span>':''}<small class="artifact-path">${esc(id.slice(0,24))}…</small></div>`).join('')}</td><td>${(a.size/1048576).toFixed(1)} MB</td><td><button class="secondary cache-select" data-id="${esc(a.id)}" ${artifactBusy||running||Object.keys(a.images).every(ref=>artifactCache.selected[ref]===a.id)?'disabled':''}>${Object.keys(a.images).every(ref=>artifactCache.selected[ref]===a.id)?'已选用':'设为使用文件'}</button><button class="secondary danger cache-delete" data-id="${esc(a.id)}" ${artifactBusy||running?'disabled':''}>删除</button></td></tr>`).join('')||'<tr><td colspan="4">暂无缓存。下载镜像或导入 docker save 的 tar / tar.gz 文件。</td></tr>';
 const chosen=new Set([...$('distributionNodes').querySelectorAll('input:checked')].map(x=>x.value));
 $('distributionNodes').innerHTML=savedNodes.map(n=>`<label><input type="checkbox" value="${esc(n.id)}" ${chosen.has(n.id)?'checked':''}>${esc(n.id)} · ${esc(n.host)} · ${n.kind==='web'?'Web':'评测机'}</label>`).join('')||'<p>先在节点配置页添加并保存服务器。</p>';
 renderImageInventory();
}
$('archiveRows').onclick=e=>{const b=e.target.closest('.cache-select');if(b)artifactGuard(async()=>{$('resourceHistory').open=false;await api('artifacts/select',{id:b.dataset.id});await refreshArtifactCache();artifactMessage('已切换本地选用归档。服务器尚未变更。');});};
function cachedImageReady(cache,ref){
 return cache.archives.some(a=>a.available!==false&&a.id===cache.selected[ref]&&a.images[ref]);
}
function missingDistributionImages(cache,nodes,ids,version){
 const kinds=new Set(nodes.filter(n=>ids.includes(n.id)).map(n=>n.kind));
 const refs=[...kinds].map(kind=>`csgrandeur/ccpcoj-${kind}2:${version}`);
 if(kinds.has('web'))refs.push(...(cache.dependencies||[]));
 return refs.filter(ref=>!cachedImageReady(cache,ref));
}
function renderImageInventory(){
 if(!artifactCache)return;
 const version=$('imageVersion').value.trim();
 const types=[['Web 程序','csgrandeur/ccpcoj-web2',version],['评测机程序','csgrandeur/ccpcoj-judge2',version],
  ...(artifactCache.dependencies||[]).map(ref=>{const [repo,tag]=ref.split(':');return [({mysql:'MySQL 数据库',nginx:'Nginx 网页入口',phpmyadmin:'phpMyAdmin 数据库管理'}[repo]||repo),repo,tag];})];
 $('imageInventory').innerHTML=types.map(([title,repo,target])=>{
  const versions=[...new Set(artifactCache.archives.filter(a=>a.available!==false).flatMap(a=>Object.keys(a.images)).filter(ref=>ref.startsWith(repo+':')).map(ref=>ref.slice(repo.length+1)))];
  const ready=target&&cachedImageReady(artifactCache,repo+':'+target);
  return `<tr><td><strong>${esc(title)}</strong><small class="artifact-path">${esc(repo)}</small></td><td>${versions.length?versions.map(esc).join('、'):'无'}</td><td>${esc(target||'尚未选择')}</td><td><span class="pill ${ready?'cache-ready':'cache-missing'}">${!target?'请选择版本':ready?'已缓存，可分发':'缺少可用的选用归档'}</span></td></tr>`;
 }).join('');
 const ids=[...$('distributionNodes').querySelectorAll('input:checked')].map(x=>x.value);
 const missing=version?missingDistributionImages(artifactCache,distributionSavedNodes,ids,version):[];
 $('distributionReadiness').textContent=!ids.length?'勾选目标后，这里会列出分发所需的缺失镜像。':!version?'请先选择本次分发版本。':missing.length?'所选 '+ids.length+' 个节点缺少：'+missing.join('、')+'。请先下载或导入。':'所选 '+ids.length+' 个节点所需镜像已缓存，分发时还会校验归档完整性。';
}
$('distributionNodes').addEventListener('change',renderImageInventory);
$('imageVersion').addEventListener('input',renderImageInventory);
$('imageVersion').addEventListener('change',renderImageInventory);
$('cachedVersions').addEventListener('change',renderImageInventory);
function renderDistributionState(s){
 if(!distributionJob||s.job!==distributionJob)return;
 $('distributionStatus').textContent='本次分发：'+s.status;
 $('distributionLogs').hidden=false;
 $('distributionLogs').textContent=s.logs.join('\n')||'正在检查本地镜像…';
}
$('imageDistribute').onclick=async()=>{
 $('resourceHistory').open=false;
 distributionJob=null;$('distributionLogs').hidden=true;
 $('distributionStatus').textContent='正在检查所选节点需要的本地镜像…';
 artifactMessage('');
 try{
  const ids=[...$('distributionNodes').querySelectorAll('input:checked')].map(x=>x.value);
  if(!ids.length)throw Error('请选择分发节点，不会默认操作全部服务器');
  const version=$('imageVersion').value.trim();if(!version)throw Error('填写本次分发版本');
  const cache=await api('artifacts/cache');cache.dependencies ||= JSON.parse($('imageInventory').dataset.dependencies);
  artifactCache=cache;renderImageInventory();
  const missing=missingDistributionImages(cache,distributionSavedNodes,ids,version);
  if(missing.length)throw Error('缺少本地镜像：'+missing.join('、')+'。Web 与评测机使用不同镜像；请在上方选择对应镜像类型，下载或导入后再分发。');
  $('version').value=version;await save();
  const r=await api('run',{action:'distribute',config,selected:ids});distributionJob=r.job;
  $('distributionStatus').textContent='分发任务已提交，实际进度如下；不会替换运行中的容器。';await poll();
 }catch(e){$('distributionStatus').textContent='分发未开始：'+e.message;}
};
function renderArtifactControls(){
 artifactButtons.forEach(id=>$(id).disabled=artifactBusy||running);
 const picked=$('imageDetected').value===''?null:detectedLocalImages[Number($('imageDetected').value)];
 $('dockerExport').disabled=artifactBusy||running||!picked||picked.source!=='docker';
 $('toolFile').disabled=artifactBusy||running;
 document.querySelectorAll('.cache-delete').forEach(b=>b.disabled=artifactBusy||running);
 document.querySelectorAll('.cache-select').forEach(b=>{const item=artifactCache?.archives.find(a=>a.id===b.dataset.id);b.disabled=artifactBusy||running||!item||item.available===false||Object.keys(item.images).every(ref=>artifactCache.selected[ref]===item.id);});
}
let artifactPolling=false;
async function artifactPoll(){
 if(artifactPolling)return;artifactPolling=true;
 try{
 const s=await api('artifacts/state');artifactBusy=s.running||!!(s.job&&s.job!==artifactSeen);
 if(s.running)$('resourceHistory').open=true;
 renderArtifactControls();
 $('artifactAction').textContent=({releases:'Release 查询',tags:'镜像版本查询',tool:'部署程序下载',pull:'镜像下载',import:'本地归档导入',discover:'本地镜像识别','docker-export':'Docker 本地镜像导出'}[s.action]||'资源任务');$('artifactState').textContent=!s.running&&artifactBusy?'正在应用任务结果…':s.status;$('artifactLogs').textContent=s.logs.join('\n')||'尚未执行资源任务。';
 if(s.running)$('artifactLogs').scrollTop=$('artifactLogs').scrollHeight;
 if(s.job&&!s.running&&s.job!==artifactSeen){
  if(s.result){
   if(s.action==='releases'){releaseRows=s.result.releases;releasesLoaded=true;$('releaseVersions').innerHTML=releaseRows.map(x=>`<option value="${esc(x.version)}">${x.prerelease?'预发布 · ':''}${x.assets.length?'可下载':'无 Linux amd64 附件'}</option>`).join('');renderReleaseAssets();artifactMessage(`查询完成，共 ${releaseRows.length} 个 Release。选择标签和附件后下载。`);}
   if(s.action==='tags'){$('downloadImageTags').innerHTML=s.result.tags.map(x=>`<option value="${esc(x.name)}">${esc(x.updated)}</option>`).join('');artifactMessage(s.result.notice);}
   if(s.action==='tool'){config.tool=s.result;$('toolVersion').value=s.result.version;config.toolVersion=s.result.version;showTool();if(config.nodes.length)await save();artifactMessage(`部署程序 ${s.result.version} 已选用：${s.result.localPath}`);}
   if(s.action==='discover'){renderDiscoveredImages(s.result);artifactMessage('本地镜像识别完成。请选择归档导入，或将已有 Docker 镜像导出到缓存。');}
   if(s.action==='docker-export'){$('imagePath').value=s.result.path;$('imageDetectStatus').textContent='已从本机 Docker 导出并登记缓存：'+s.result.path;}
   if(['pull','import','docker-export'].includes(s.action)){await refreshArtifactCache();artifactMessage('本地镜像缓存已就绪。选择节点后可独立分发，也可直接执行部署或升级。');}
  }else {if(['pull'].includes(s.action))await refreshArtifactCache();artifactMessage(s.status);if(s.action==='releases')$('releaseHint').textContent='仓库查询失败，请重试。'+s.status;}
  artifactSeen=s.job;
 }
 artifactBusy=s.running;$('artifactState').textContent=s.status;renderArtifactControls();
 }finally{artifactPolling=false;}
}
document.querySelector('[data-tab="artifacts"]').addEventListener('click',()=>artifactGuard(async()=>{await refreshArtifactCache();await artifactPoll();}));
document.addEventListener('console-ready',()=>artifactGuard(async()=>{await artifactPoll();renderReleaseAssets();showSourceOrder();}));
for(const id of ['imageVersion','imageMirror'])$(id).addEventListener('input',invalidatePlan);
setInterval(()=>artifactPoll().catch(()=>{}),1500);
window.addEventListener('beforeunload',e=>{if(artifactBusy){e.preventDefault();e.returnValue='资源任务仍在运行';}});

let detectedLocalImages=[],localDiscoveryResult=null;
function discoveredVersion(ref){return ref.slice(ref.lastIndexOf(':')+1);}
function compareLocalVersions(a,b){
 const av=discoveredVersion(a.ref||Object.keys(a.images)[0]),bv=discoveredVersion(b.ref||Object.keys(b.images)[0]);
 return bv.localeCompare(av,undefined,{numeric:true});
}
function renderDiscoveredImages(data){
 localDiscoveryResult=data;
 const archives=[...data.archives].sort(compareLocalVersions);
 const docker=[...data.docker.images].sort(compareLocalVersions);
 detectedLocalImages=[...archives.map(a=>({...a,source:'archive'})),...docker.map(a=>({...a,source:'docker'}))];
 $('imageDetected').innerHTML='<option value="">请选择镜像版本与来源</option>'+detectedLocalImages.map((a,i)=>`<option value="${i}">${esc(a.source==='archive'?'归档 · '+Object.keys(a.images).join(', ')+' · '+a.path:'Docker · '+a.ref+' · '+a.platform+' · '+a.id.slice(0,19))}</option>`).join('');
 const target=$('imageVersion').value.trim();
 const kind=$('imageKind').value;
 const wanted=ref=>discoveredVersion(ref)===target&&(kind==='both'||ref.startsWith('csgrandeur/ccpcoj-'+kind+'2:'));
 const matching=detectedLocalImages.map((a,i)=>({a,i})).filter(({a})=>a.source==='docker'?wanted(a.ref):Object.keys(a.images).some(wanted));
 // Prefer a verified on-disk archive over an export of the same target.
 const fileMatches=matching.filter(({a})=>a.source==='archive');
 const preferred=fileMatches.length?fileMatches:matching;
 if(!$('imagePath').value.trim() && preferred.length===1){$('imageDetected').value=String(preferred[0].i);selectDiscoveredImage(false);}
 else $('dockerExport').disabled=true;
 $('imageDetectStatus').textContent=`磁盘归档 ${archives.length} 个；Docker 镜像 ${docker.length} 个（含 OJ 应用和 MySQL / Nginx / phpMyAdmin 依赖）。${data.docker.message} ${target&&!matching.length?'未找到当前目标版本 '+target+'，请明确选择其他版本。':''}${preferred.length>1?'当前版本有多个用途或来源，请在列表中选择。':''}${data.truncated?'扫描已达 300 个候选上限。':''} 扫描跳过 ${data.rejected.length} 个不支持的归档。`;
 $('imageScanDetails').textContent='磁盘扫描目录：'+data.roots.join('；')+'。识别 Linux amd64 的 OJ 应用及 MySQL / Nginx / phpMyAdmin 依赖。Docker 已有镜像需导出并导入缓存后，下方才显示“已缓存，可分发”；依赖使用各自版本，不使用 OJ 的版本号。';
}
function selectDiscoveredImage(persist=true){
 const value=$('imageDetected').value;
 const image=value===''?null:detectedLocalImages[Number(value)];
 $('dockerExport').disabled=!image||image.source!=='docker'||artifactBusy||running;
 if(!image){$('imagePath').value='';return;}
 const refs=image.source==='archive'?Object.keys(image.images):[image.ref];
 const apps=refs.filter(ref=>/^csgrandeur\/ccpcoj-(web|judge)2:/.test(ref));
 const versions=[...new Set(apps.map(discoveredVersion))];
 if(persist&&versions.length===1){
  $('imageVersion').value=versions[0];
  $('imageKind').value=apps.some(ref=>ref.startsWith('csgrandeur/ccpcoj-web2:'))?(apps.some(ref=>ref.startsWith('csgrandeur/ccpcoj-judge2:'))?'both':'web'):'judge';
  invalidatePlan();autoSaveSettings();renderImageInventory();
 }
 if(image.source==='archive'){$('imagePath').value=image.path;}
 else {$('imagePath').value='';$('imageDetectStatus').textContent='已选择本机 Docker 镜像 '+image.ref+'。点击“导出所选 Docker 镜像并导入缓存”后会填入真实归档路径。';}
}
async function detectLocalImages(){
 $('imageDetectStatus').textContent='正在后台检查磁盘归档和本机 Docker…';
 await artifactStart('discover');
}
$('imageDetected').onchange=selectDiscoveredImage;
$('imageDetect').onclick=()=>artifactGuard(detectLocalImages);
for(const id of ['imageVersion','imageKind'])$(id).addEventListener('change',()=>{
 $('imagePath').value='';$('imageDetected').value='';
 if(localDiscoveryResult)renderDiscoveredImages(localDiscoveryResult);
});
$('dockerExport').onclick=()=>artifactGuard(async()=>{
 const image=detectedLocalImages[Number($('imageDetected').value)];
 if(!image||image.source!=='docker')throw Error('先选择一个本机 Docker 镜像');
 await artifactStart('docker-export',{ref:image.ref,imageId:image.id});
});
