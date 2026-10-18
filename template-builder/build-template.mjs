import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await fs.mkdir(path.join(root,'templates'),{recursive:true});
const wb=Workbook.create(),nodes=wb.worksheets.add('节点'),settings=wb.worksheets.add('全局设置');
const columns=['类型','节点名称','服务器IP','备用(0/1)','SSH账号','SSH端口','SSH密码','提权方式','提权密码','私钥名称','项目目录','绑定Web','站点标识','Web端口','管理端口','管理员账号','管理员口令','pods','评测账号'];
nodes.getRange('A2').values=[['CSGOJ 节点导入模板']];
nodes.getRange('A3').values=[['填写 IP 后导入；删除不需要的示例行。备用：0=启用，1=备用。密码可留空后在界面补填。']];
nodes.getRange('A5:S5').values=[columns];
nodes.getRange('A6:S9').values=[
 ['web','web01','',0,'ccpc00',22,'','sudo','','web-exp','/home/ccpc00/cpc','','web01',20080,20050,'admin','',null,''],
 ['web','web02','',1,'ccpc00',22,'','sudo','','web-backup','/home/ccpc00/cpc','','web02',20080,20050,'admin','',null,''],
 ['judge','pcj01','',0,'ccpc00',22,'','sudo','','pcj01','/home/ccpc00/cpc','web01','',null,null,'','',1,'j1'],
 ['judge','pcj02','',1,'ccpc00',22,'','sudo','','pcj02','/home/ccpc00/cpc','web01','',null,null,'','',1,'j2'],
];
nodes.showGridLines=false;nodes.tabColor='#2563EB';
nodes.getRange('A1:S30').format.font={name:'Arial',size:11,color:'#263548'};
nodes.getRange('A2:S2').format.font={name:'Arial',size:16,bold:true,color:'#175DCD'};
nodes.getRange('A2:S2').format.rowHeight=32;
nodes.getRange('A3:S3').format.rowHeight=25;
nodes.getRange('A3:S3').format.font={name:'Arial',size:10,color:'#69788C'};
nodes.getRange('A5:S5').format={fill:'#2169D8',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:32,horizontalAlignment:'center',verticalAlignment:'center'};
nodes.getRange('A6:S30').format.rowHeight=29;
nodes.getRange('A6:S30').format.verticalAlignment='center';
nodes.getRange('A6:S30').format.numberFormat='@';
for(const col of ['D','F','N','O','R'])nodes.getRange(`${col}6:${col}30`).setNumberFormat('0');
nodes.getRange('A5:S30').format.columnWidth=17;
nodes.getRange('C5:C30').format.columnWidth=20;
nodes.getRange('G5:G30').format.columnWidth=24;
nodes.getRange('I5:I30').format.columnWidth=24;
nodes.getRange('K5:K30').format.columnWidth=29;
nodes.getRange('Q5:Q30').format.columnWidth=24;
nodes.getRange('C6:C9').format.fill='#FFF2CF';
nodes.getRange('A6:S30').format.borders={bottom:{style:'thin',color:'#E3EAF5'}};
nodes.getRange('A6:A1000').dataValidation={rule:{type:'list',values:['web','judge']}};
nodes.getRange('D6:D1000').dataValidation={rule:{type:'list',values:['0','1']}};
nodes.getRange('H6:H1000').dataValidation={rule:{type:'list',values:['sudo','su','root']}};
nodes.freezePanes.freezeRows(5);nodes.freezePanes.freezeColumns(2);
settings.showGridLines=false;settings.tabColor='#91B8EF';
settings.getRange('A2').values=[['全局设置']];
settings.getRange('A4:C11').values=[
 ['字段','值','说明'],
 ['镜像标签','2.0.281-exp','按需要填写镜像标签；不自动追踪最新版'],
 ['部署工具Release','2.0.271','下载到 Windows 后，由工具上传服务器'],
 ['国内镜像源','','填写 HTTPS 镜像域名，留空保留 Docker 原设置'],
 ['关闭图形界面',1,'1=关闭图形登录，0=不修改；当前桌面会退出'],
 ['永不休眠',1,'1=禁用系统睡眠、挂起和休眠，0=不修改'],
 ['性能模式',1,'1=尝试设置支持的性能模式，0=不修改'],
 ['填写提示','','节点表密码是明文；可留空，在导入后的页面补填'],
];
settings.getRange('A1:C16').format.font={name:'Arial',size:11,color:'#263548'};
settings.getRange('A2:C2').format.font={name:'Arial',size:16,bold:true,color:'#175DCD'};
settings.getRange('A2:C2').format.rowHeight=32;
settings.getRange('A1:C16').format.verticalAlignment='center';
settings.getRange('A4:C4').format={fill:'#2169D8',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:30};
settings.getRange('A5:C11').format.rowHeight=30;
settings.getRange('A4:A11').format.columnWidth=25;settings.getRange('B4:B11').format.columnWidth=32;settings.getRange('C4:C11').format.columnWidth=76;
settings.getRange('B5:B10').format.fill='#F0F6FF';settings.getRange('B8:B10').setNumberFormat('0');
settings.getRange('B8:B10').dataValidation={rule:{type:'list',values:['0','1']}};
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'节点!A5:F9',include:'values',tableMaxRows:5,tableMaxCols:6,maxChars:2000})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:10},maxChars:1000})).ndjson);
for(const [sheet,range,file] of [['节点','A2:F10','template-nodes.png'],['全局设置','A2:C11','template-settings.png']]){
 const image=await wb.render({sheetName:sheet,range,scale:1.3,format:'png'});
 await fs.writeFile(path.join(root,'verification',file),new Uint8Array(await image.arrayBuffer()));
}
const output=await SpreadsheetFile.exportXlsx(wb);await output.save(path.join(root,'templates','CSGOJ节点导入模板.xlsx'));
console.log('Template exported');
