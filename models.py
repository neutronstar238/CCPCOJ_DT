"""Configuration and deployment plans. No network operations."""
import copy
import re
import secrets
from pathlib import PurePosixPath
from urllib.parse import urlsplit

IMAGES = ['mysql:8.0.32', 'nginx:1.29.7-alpine', 'phpmyadmin:5.2.3']

def default_config():
    return dict(version='', toolVersion='', mirror='',
                disableGui=True, neverSleep=True, performance=True, nodes=[])

def normalize(raw):
    c = copy.deepcopy(raw)
    for field in ('version', 'toolVersion'):
        if c.get(field, '') and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', c[field]):
            raise ValueError('版本格式错误：' + field)
    mirror = c.get('mirror', '').strip().rstrip('/')
    if mirror:
        u = urlsplit(mirror if '://' in mirror else 'https://' + mirror)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError('镜像源请填写 HTTPS 域名，可带端口，不含路径和密码')
        c['mirror'] = 'https://' + u.netloc
    ids, keys, placements = set(), set(), set()
    if not isinstance(c.get('nodes'), list):
        raise ValueError('节点列表格式错误')
    for n in c['nodes']:
        if n.get('targetVersion','') and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',n['targetVersion']):
            raise ValueError('节点目标版本格式错误：'+n.get('id',''))
        for f in ('id', 'keyName'):
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', n.get(f, '')):
                raise ValueError(f'{f} 只能使用字母、数字、下划线和短横线，首位为字母')
        if n['id'] in ids or n['keyName'] in keys:
            raise ValueError('节点名称和私钥名称不能重复')
        ids.add(n['id']); keys.add(n['keyName'])
        if n.get('kind') not in ('web', 'judge') or n.get('role') not in ('active', 'standby'):
            raise ValueError('节点类型或角色错误')
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', n.get('host', '')):
            raise ValueError(n['id'] + '：填写 IPv4 地址或 DNS 主机名')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', n.get('user', '')):
            raise ValueError('SSH 用户名格式错误')
        n['sshPort'] = int(n.get('sshPort', 22))
        if not 1 <= n['sshPort'] <= 65535: raise ValueError('SSH 端口错误')
        path = n.get('path', '')
        p = PurePosixPath(path)
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in p.parts or len(p.parts) < 3 or p.parts[1] not in ('home', 'srv', 'opt', 'mnt'):
            raise ValueError('项目目录须为 /home、/srv、/opt 或 /mnt 下的专用绝对目录，不含空格和 ..')
        n['path'] = str(p)
        location = (n['host'], n['sshPort'], str(p))
        if location in placements: raise ValueError('同一主机的不同节点须使用不同项目目录')
        placements.add(location)
        if n.get('elevation', 'sudo') not in ('sudo', 'su', 'root'): raise ValueError('提权方式错误')
        if n.get('elevation') == 'root' and n['user'] != 'root': raise ValueError('直接 root 模式要求 SSH 用户为 root')
        n.setdefault('password', ''); n.setdefault('elevatePassword', '')
        if n['kind'] == 'web':
            db = n.get('databasePath','').strip()
            if db:
                dp=PurePosixPath(db)
                if (not re.fullmatch(r'/[A-Za-z0-9_./-]+',db) or '..' in dp.parts or len(dp.parts)<3
                        or dp.parts[1] in ('etc','proc','sys','dev','run','boot','bin','usr','var')
                        or dp.is_relative_to(p) or p.is_relative_to(dp)):
                    raise ValueError('数据库目录须为项目之外的专用绝对目录，例如 /data-ssd/mysql/ccpc')
                n['databasePath']=str(dp)
            n.setdefault('site', re.sub('[^a-z0-9]', '', n['id'].lower()))
            if not re.fullmatch('[a-z][a-z0-9]{0,19}', n['site']): raise ValueError('站点标识须为小写字母数字，最多 20 位')
            for f, d in [('port', 20080), ('myadminPort', 20050)]:
                n[f] = int(n.get(f, d))
                if not 1 <= n[f] <= 65535: raise ValueError('Web 端口应在 1–65535 内')
            if n['port'] == n['myadminPort']: raise ValueError('Web 与 phpMyAdmin 端口冲突')
            if not re.fullmatch(r'[A-Za-z0-9_]{5,20}', n.get('adminUser', 'admin')): raise ValueError('管理员账号须为 5–20 位字母数字下划线')
            n.setdefault('adminUser', 'admin')
            n.setdefault('secrets', {})
            for k in ('sqlRoot', 'sqlUser', 'myadmin', 'admin'):
                if not n['secrets'].get(k): n['secrets'][k] = secrets.token_hex(20)
            if len(n['secrets']['admin']) < 8: raise ValueError('管理员初始口令至少 8 位')
        else:
            n['pods'] = int(n.get('pods', 1))
            if not 1 <= n['pods'] <= 16: raise ValueError('pods 必须在 1–16 之间；执行时还会检查 CPU 和内存')
            if 'desiredPods' in n:
                n['desiredPods'] = int(n['desiredPods'])
                if not 1 <= n['desiredPods'] <= 16: raise ValueError('目标 pods 必须在 1–16 之间')
            n.setdefault('judgePassword', secrets.token_hex(12))
    webs = {n['id']: n for n in c['nodes'] if n['kind'] == 'web'}
    used_accounts = set()
    ports = set()
    for n in c['nodes']:
        if n['kind'] == 'web':
            for port in (n['port'], n['myadminPort']):
                item = (n['host'], port)
                if item in ports: raise ValueError('同一主机的 Web 端口冲突')
                ports.add(item)
        else:
            if n.get('web') not in webs: raise ValueError(n['id'] + '：请选择绑定 Web')
            if n['role'] == 'active' and webs[n['web']]['role'] != 'active': raise ValueError('启用的评测机只能绑定启用的 Web')
            if not n.get('judgeUser'):
                existing = {x.get('judgeUser') for x in c['nodes'] if x.get('web') == n['web']}
                n['judgeUser'] = next((f'j{i}' for i in range(1, 100) if f'j{i}' not in existing), '')
            if not re.fullmatch('[a-z]{1,2}[0-9]{1,2}', n['judgeUser']): raise ValueError('评测账号要求 1–2 位小写字母加 1–2 位数字')
            item = (n['web'], n['judgeUser'])
            if item in used_accounts: raise ValueError('同一 Web 上的评测账号不能重复')
            used_accounts.add(item)
    return c

def plan(c):
    rows = []
    for n in sorted(c['nodes'], key=lambda n: n['kind'] != 'web'):
        steps = ['SSH 指纹登记/校验', '本机创建或复用密钥，追加公钥并验证', '检查 Ubuntu 24.04 x64', '创建项目目录，上传并校验官方工具', '安装/启动 Docker，从 Windows 分发并校验镜像']
        if c.get('disableGui'): steps.append('记录图形状态，关闭图形登录（当前桌面会退出）')
        if c.get('neverSleep'): steps.append('记录原设置，禁用系统睡眠、挂起和休眠（永不休眠）')
        if c.get('performance'): steps.append('记录并设置受支持的 performance 模式')
        steps.append('断网容器检查；不启动站点，不注册评测账号' if n['role'] == 'standby' else ('官方 up 部署、初始化管理员、检查 CSS 和登录' if n['kind'] == 'web' else '创建专用评测账号、官方 judge 部署、检查进程和 OJ 心跳'))
        rows.append(dict(id=n['id'], host=n['host'], role=n['role'], kind=n['kind'], path=n['path'], steps=steps))
    return rows

def protect_managed(previous, current):
    """Prevent editing a saved managed identity into an unrelated deployment."""
    for old in (previous or {}).get('nodes', []):
        if not old.get('prepared'): continue
        new = next((n for n in current['nodes'] if n['id'] == old['id']), None)
        if not new: raise ValueError('已准备节点不能直接删除或改名：' + old['id'])
        for field in ('kind', 'path', 'databasePath', 'keyName', 'role', 'site', 'port', 'myadminPort', 'pods', 'judgeUser', 'judgePassword', 'secrets', 'adminUser'):
            if (old.get(field,'') if field=='databasePath' else old.get(field)) != (new.get(field,'') if field=='databasePath' else new.get(field)): raise ValueError('已准备节点的 ' + field + ' 不能直接修改；数据库位置请使用数据库迁移，角色请使用备用启用')
        for field in ('prepared', 'preparedVersion', 'deployed', 'deployedVersion', 'retainedData', 'adopted', 'projectStorage'):
            if field in old: new[field] = old[field]
    return current

def redact(text, config):
    values = []
    for n in config.get('nodes', []):
        values += [n.get('password'), n.get('elevatePassword'), n.get('judgePassword')]
        values += list(n.get('secrets', {}).values())
    for v in sorted((str(x) for x in values if x), key=len, reverse=True): text = text.replace(v, '[已隐藏]')
    return text
