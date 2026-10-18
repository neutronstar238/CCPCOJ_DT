'use strict';
let deletingArchive=null,archiveDeleteBusy=false;
$('archiveRows').addEventListener('click',e=>{
 const button=e.target.closest('.cache-delete');
 if(!button||running||artifactBusy||archiveDeleteBusy)return;
 const item=artifactCache?.archives.find(a=>a.id===button.dataset.id);if(!item)return;
 deletingArchive=item.id;
 $('archiveDeleteDetails').textContent=`文件：${item.name}\n大小：${(item.size/1048576).toFixed(1)} MB\n镜像：\n${Object.keys(item.images).join('\n')}`;
 const selectedRefs=Object.keys(item.images).filter(ref=>artifactCache.selected[ref]===item.id);
 $('archiveDeleteImpact').textContent=selectedRefs.length?'该归档正在选用。删除后将清除这些镜像的选用记录；再次分发前需重新下载、导入或选择其他归档。':'删除后将从本机缓存列表移除该文件。';
 $('archiveDeleteError').textContent='';$('archiveDeleteDialog').showModal();$('archiveDeleteCancel').focus();
});
$('archiveDeleteCancel').onclick=()=>{if(!archiveDeleteBusy)$('archiveDeleteDialog').close();};
$('archiveDeleteDialog').addEventListener('cancel',e=>{if(archiveDeleteBusy)e.preventDefault();});
$('archiveDeleteDialog').addEventListener('close',()=>{deletingArchive=null;});
$('archiveDeleteConfirm').onclick=async()=>{
 if(!deletingArchive||archiveDeleteBusy)return;
 archiveDeleteBusy=true;$('archiveDeleteConfirm').disabled=true;$('archiveDeleteCancel').disabled=true;
 try{
  await api('artifacts/delete',{id:deletingArchive,confirmed:true});
  $('archiveDeleteDialog').close();await refreshArtifactCache();
  artifactMessage('本地缓存归档已删除，选用记录已同步刷新。服务器镜像、运行服务及原始导入文件未改动。');
 }catch(e){if($('archiveDeleteDialog').open)$('archiveDeleteError').textContent=e.message;else artifactMessage('删除已完成，但刷新失败：'+e.message);}
 finally{archiveDeleteBusy=false;$('archiveDeleteConfirm').disabled=false;$('archiveDeleteCancel').disabled=false;}
};
