"""Bounded XLSX input reader; supports standard Excel/WPS inline/shared strings.
No macros, external links, formula evaluation, arbitrary extraction or third-party runtime.
"""
import copy
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from models import default_config, normalize, protect_managed

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
FIELDS=['类型','节点名称','服务器IP','备用(0/1)','SSH账号','SSH端口','SSH密码','提权方式','提权密码','私钥名称','项目目录','绑定Web','站点标识','Web端口','管理端口','管理员账号','管理员口令','pods','评测账号']
SETTINGS={'镜像标签':'version','部署工具Release':'toolVersion','国内镜像源':'mirror','关闭图形界面':'disableGui','永不休眠':'neverSleep','性能模式':'performance'}

def sheets(binary):
    if len(binary)>8*1024**2:raise ValueError('XLSX 文件不能超过 8 MB')
    try:z=zipfile.ZipFile(io.BytesIO(binary))
    except zipfile.BadZipFile:raise ValueError('不是有效的 XLSX 文件；请使用随包模板')
    with z:
        infos=z.infolist()
        if len(infos)>500 or sum(i.file_size for i in infos)>24*1024**2:raise ValueError('工作簿过大，请只保留部署配置')
        if any('vbaProject' in i.filename or 'externalLinks/' in i.filename for i in infos):raise ValueError('不接受宏或外部链接工作簿')
        def xml(name):
            raw=z.read(name)
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('不支持 XML 实体声明')
            return ET.fromstring(raw)
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(e.itertext()) for e in xml('xl/sharedStrings.xml').findall('m:si',NS)]
        rels={e.attrib['Id']:e.attrib['Target'] for e in xml('xl/_rels/workbook.xml.rels')}
        result={}
        for sheet in xml('xl/workbook.xml').findall('m:sheets/m:sheet',NS):
            name=sheet.attrib['name']
            if name not in ('节点','全局设置'):continue
            rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target=rels[rid]
            path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if not path.startswith('xl/worksheets/'):raise ValueError('不支持的工作表路径')
            rows=[]
            for row in xml(path).findall('m:sheetData/m:row',NS):
                if int(row.attrib.get('r',0))>2000:raise ValueError('最多支持 2000 行配置')
                cells={}
                for cell in row.findall('m:c',NS):
                    if cell.find('m:f',NS) is not None:raise ValueError(name+' 表不接受公式，请粘贴为值')
                    letters=re.match('[A-Z]+',cell.attrib.get('r','')).group()
                    col=0
                    for x in letters:col=col*26+ord(x)-64
                    if col>30:continue
                    value=cell.find('m:v',NS);value=value.text if value is not None else ''
                    kind=cell.attrib.get('t')
                    if kind=='s':value=strings[int(value)]
                    elif kind=='inlineStr':value=''.join(e.text or '' for e in cell.findall('.//m:t',NS))
                    elif kind=='e':raise ValueError(name+' 表存在 Excel 错误值')
                    cells[col-1]=value or ''
                rows.append([cells.get(i,'') for i in range(max(cells,default=-1)+1)])
            result[name]=rows
        if '节点' not in result:raise ValueError('找不到名为“节点”的工作表，请使用模板')
        return result

def flag(value,field):
    if str(value).strip() not in ('0','1'):raise ValueError(field+' 只能填写 0 或 1')
    return str(value).strip()=='1'

def preview(binary,base):
    data=sheets(binary);c=copy.deepcopy(base or default_config());c.setdefault('nodes',[])
    for row in data.get('全局设置',[]):
        if len(row)<2 or row[0] not in SETTINGS:continue
        key=SETTINGS[row[0]];value=row[1].strip()
        c[key]=flag(value,row[0]) if key in ('disableGui','neverSleep','performance') else value
    header=None;changes=[];seen=set()
    for index,row in enumerate(data['节点'],1):
        if '节点名称' in row and '服务器IP' in row:
            if len(set(row))!=len(row):raise ValueError('节点表有重复列名')
            header=row
            if set(FIELDS)-set(header):raise ValueError('模板列名不完整：'+','.join(set(FIELDS)-set(header)))
            continue
        if header is None or not any(str(x).strip() for x in row):continue
        values=dict(zip(header,row));get=lambda k: str(values.get(k,'')).strip()
        nid=get('节点名称')
        if not nid:raise ValueError(f'节点表第 {index} 行缺少节点名称')
        if nid in seen:raise ValueError('Excel 中节点名称重复：'+nid)
        seen.add(nid)
        previous=next((n for n in c['nodes'] if n['id']==nid),None)
        n=copy.deepcopy(previous) if previous else dict(id=nid,sshPort=22,user='ccpc00',path='/home/ccpc00/cpc',elevation='sudo',keyName=nid)
        kind=get('类型').lower()
        if kind not in ('web','judge'):raise ValueError(f'第 {index} 行类型只能为 web 或 judge')
        n.update(kind=kind,host=get('服务器IP'),role='standby' if flag(get('备用(0/1)'),'备用') else 'active')
        for title,key in [('SSH账号','user'),('SSH端口','sshPort'),('SSH密码','password'),('提权方式','elevation'),('提权密码','elevatePassword'),('私钥名称','keyName'),('项目目录','path')]:
            if get(title):n[key]=str(values.get(title,'')) if key in ('password','elevatePassword') else get(title)
        if kind=='web':
            for title,key in [('站点标识','site'),('Web端口','port'),('管理端口','myadminPort'),('管理员账号','adminUser')]:
                if get(title):n[key]=get(title)
            if get('管理员口令'):n.setdefault('secrets',{})['admin']=get('管理员口令')
        else:
            n['web']=get('绑定Web')
            for title,key in [('pods','pods'),('评测账号','judgeUser')]:
                if get(title):n[key]=get(title)
        if previous:c['nodes'][c['nodes'].index(previous)]=n
        else:c['nodes'].append(n)
        changes.append(dict(id=nid,kind=kind,host=n['host'],role=n['role'],action='更新' if previous else '新增'))
    if not header or not changes:raise ValueError('节点表没有可导入的节点')
    c=protect_managed(base,normalize(c))
    return dict(config=c,changes=changes,notice='按节点名称合并，未列出的现有节点保留。确认导入只保存配置，不连接服务器。')
