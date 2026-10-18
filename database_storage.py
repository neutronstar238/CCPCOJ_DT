"""Cold MySQL directory relocation. Keeps the official container bind path stable."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

FSTAB = Path('/etc/fstab')
DROPINS = Path('/etc/systemd/system/docker.service.d')

def is_mount(path):
    # os.path.ismount misses bind mounts on the same filesystem.
    return subprocess.run(['mountpoint','-q','--',str(path)]).returncode == 0

def dbpath(root):
    return root / 'data/var/mysql/mysql_data'

def validate_destination(value, root):
    p = Path(value)
    if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', value or '') or '..' in p.parts
            or len(p.parts) < 3 or p.parts[1] in ('etc', 'proc', 'sys', 'dev', 'run', 'boot', 'bin', 'usr', 'var')
            or p.is_relative_to(root) or root.is_relative_to(p) or p.resolve() != p):
        raise ValueError('数据库目标须为项目目录之外的专用绝对目录，不含空格、.. 或符号链接，例如 /data-ssd/mysql/ccpc')
    return p

def location(ops, path):
    p = Path(path)
    existing = p
    while not existing.exists():
        existing = existing.parent
    item = json.loads(ops.run(['findmnt', '-J', '-T', str(existing), '-o', 'TARGET,SOURCE,FSTYPE,UUID']))['filesystems'][0]
    return dict(path=str(p), realPath=str(p.resolve()), exists=p.exists(), mount=item['target'],
                device=item['source'], filesystem=item['fstype'], uuid=item.get('uuid'),
                freeBytes=shutil.disk_usage(existing).free)

def inspect(ops, n, root):
    if n['kind'] != 'web':
        raise ValueError('存储检查仅适用于 Web')
    paths = [('project', '项目及部署程序', root), ('database', 'MySQL 数据', dbpath(root)),
             ('code', 'Web 代码 /var/www', root/'data/var/www'),
             ('problems', '题库 /home/judge', root/('data/var/data/judge-'+n['site']))]
    result = dict(locations=[dict(key=k, label=label, **location(ops, p)) for k, label, p in paths])
    if ops.docker_exists():
        docker_root = ops.run(['docker', 'info', '--format', '{{.DockerRootDir}}'])
        result['locations'].append(dict(key='docker', label='Docker 镜像及容器层', **location(ops, docker_root)))
        for c in ops.inspect_containers(n):
            if c['name'] == n['site']+'db':
                result['databaseMounts'] = c['mounts']
    state = root/'private/database-storage.json'
    result['managedStorage'] = json.loads(state.read_text()) if state.exists() else None
    result['pending'] = (root/'private/database-migration.json').exists()
    return result

def assert_project(ops,n,root):
    expected=n.get('projectStorage')
    if not expected:return
    current=location(ops,root)
    if (current['uuid']!=expected.get('uuid') or current['mount']!=expected.get('mount')
            or current['device'].partition('[')[2]!=expected.get('device','').partition('[')[2] or not current['exists']):
        raise RuntimeError('项目 SSD 挂载与已记录的位置不一致，已阻止操作；请恢复原磁盘挂载后重试')

def plan(ops, n, root, target, initializing=False):
    if n['kind'] != 'web': raise ValueError('只支持 Web 数据库目录')
    dest = validate_destination(target, root)
    src = dbpath(root)
    ancestor = dest
    while ancestor != ancestor.parent:
        if ancestor.exists() and root.exists() and os.path.samefile(ancestor,root):
            raise RuntimeError('目标通过挂载别名指向项目内部，不能迁移到自身')
        if ancestor.exists() and src.exists() and os.path.samefile(ancestor,src):
            raise RuntimeError('目标在现有数据库内部，拒绝递归复制')
        ancestor = ancestor.parent
    if (root/'private/database-migration.json').exists(): raise RuntimeError('存在未完成数据库迁移，请先恢复')
    if (root/'private/update-transaction.json').exists(): raise RuntimeError('存在未完成镜像更新，请先恢复')
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise RuntimeError('目标目录非空；不会覆盖现有数据')
    if not shutil.which('rsync'): raise RuntimeError('服务器缺少 rsync，请先安装：sudo apt-get install rsync')
    target_info = location(ops, dest)
    if target_info['filesystem'] not in ('ext4', 'xfs', 'btrfs'):
        raise RuntimeError('数据库目标仅支持本地 ext4 / xfs / btrfs 文件系统')
    # A separate disk must have a persistent mount. Never create data on an absent SSD.
    mount = target_info['mount']
    entries = [line.split() for line in FSTAB.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
    for row in entries:
        if len(row)>3 and row[1].startswith('/') and dest.is_relative_to(Path(row[1])) and len(row[1])>len(mount):
            raise RuntimeError('目标目录所属磁盘未按 fstab 挂载；不会在系统盘创建替代目录')
    if mount != '/' and not any(len(row)>3 and row[1] == mount and 'noauto' not in row[3].split(',') for row in entries):
        raise RuntimeError('目标磁盘尚无开机自动挂载记录；请先按 UUID 挂载 SSD，再选择其下的专用目录')
    managed = root/'private/database-storage.json'
    previous = json.loads(managed.read_text()) if managed.exists() else None
    if is_mount(src) and not previous:
        raise RuntimeError('数据库目录已有非本工具管理的独立挂载；请先核对挂载，项目根目录整体挂载不受此限制')
    identity = []
    if initializing:
        if ops.inspect_containers(n) or (src.exists() and any(src.iterdir())):
            raise RuntimeError('初始化数据库目录要求没有既有容器和数据库')
    else:
        ops.owned(n, root)
        cs = ops.inspect_containers(n)
        if len(cs) != len(ops.names(n)): raise RuntimeError('迁移要求完整的原 Web 容器')
        for c in cs:
            full = json.loads(ops.run(['docker','inspect',c['name']]))[0]
            identity.append(full['Id'])
            if c['name'] == n['site']+'db' and not any(m['Type']=='bind' and m['Source']==str(src) and m['Destination']=='/var/lib/mysql' for m in c['mounts']):
                raise RuntimeError('MySQL 实际目录与官方目录不符，拒绝猜测迁移')
        if not (src/'auto.cnf').is_file(): raise RuntimeError('未识别到 MySQL 数据目录')
    size = int(ops.run(['du','-sb',str(src)]).split()[0]) if src.exists() else 0
    if target_info['freeBytes'] < size + max(size//10, 256*1024**2):
        raise RuntimeError('目标空间不足，需数据库大小外加至少 10% / 256 MiB 余量')
    source_info = location(ops, src)
    pin = dict(target=target_info, source=source_info, containers=identity, previous=previous)
    for info in (pin['target'],pin['source']): info.pop('freeBytes',None);info.pop('exists',None)
    fingerprint = hashlib.sha256(json.dumps(pin,sort_keys=True).encode()).hexdigest()
    return dict(target=str(dest), source=str(src), bytes=size, fingerprint=fingerprint,
                sourceLocation=source_info, targetLocation=target_info,
                freeBytes=location(ops,dest)['freeBytes'], previous=previous,
                sameDisk=source_info['uuid']==target_info['uuid'])

def paths(root):
    ident=hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return root/'private/database-migration.json', DROPINS/('csgoj-db-'+ident+'.conf'), 'CSGOJ-DATABASE-'+ident

def atomic_text(path, text):
    tmp=path.with_name(path.name+'.csg-'+uuid.uuid4().hex)
    try:
        with tmp.open('x') as f:
            f.write(text);f.flush();os.fsync(f.fileno())
        tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

def install_mount(ops, root, dest):
    src=dbpath(root); journal, dropin, tag=paths(root)
    lines=FSTAB.read_text().splitlines(); result=[]; inside=False
    for line in lines:
        if line == '# BEGIN '+tag: inside=True;continue
        if line == '# END '+tag: inside=False;continue
        if not inside: result.append(line)
    if inside: raise RuntimeError('fstab 中数据库挂载块不完整')
    result += ['# BEGIN '+tag, f'{dest} {src} none bind,x-systemd.requires-mounts-for={dest} 0 0', '# END '+tag]
    atomic_text(FSTAB,'\n'.join(result)+'\n')
    DROPINS.mkdir(parents=True,exist_ok=True)
    atomic_text(dropin,'[Unit]\nRequiresMountsFor='+str(src)+' '+str(dest)+'\n')
    ops.run(['systemctl','daemon-reload'])
    if is_mount(src): ops.run(['umount',str(src)])
    ops.run(['mount','--bind',str(dest),str(src)])
    if not os.path.samefile(src,dest): raise RuntimeError('新数据库挂载未生效')

def begin(ops, n, root, extra, initializing=False):
    p=plan(ops,n,root,extra['target'],initializing)
    if not initializing and p['fingerprint']!=extra.get('fingerprint'): raise RuntimeError('磁盘或容器配置已变化，请重新检查迁移')
    cs=ops.inspect_containers(n)
    if any(x['running'] for x in cs): raise RuntimeError('冷迁移要求全部 Web 容器已停止')
    if not initializing:
        state=json.loads(ops.run(['docker','inspect',n['site']+'db']))[0]['State']
        if state['ExitCode']!=0: raise RuntimeError('MySQL 未正常退出，不能复制数据')
    dest=Path(p['target']);src=dbpath(root);journal,dropin,tag=paths(root)
    tx=dict(plan=p,phase='copying',fstabBefore=FSTAB.read_text(),dropinBefore=dropin.read_text() if dropin.exists() else None,
            initializing=initializing,created=time.time())
    ops.write_json(journal,tx)
    dest.mkdir(parents=True,exist_ok=True);src.mkdir(parents=True,exist_ok=True)
    print('数据库迁移 1/3：离线复制（保留原数据）',flush=True)
    ops.run(['rsync','-aHAX','--numeric-ids',str(src)+'/',str(dest)+'/'])
    print('数据库迁移 2/3：逐文件校验内容及权限',flush=True)
    differences=ops.run(['rsync','-aHAXnc','--numeric-ids','--delete','--itemize-changes',str(src)+'/',str(dest)+'/'])
    if differences: raise RuntimeError('复制校验不一致，原数据保留：'+differences[:800])
    tx['phase']='switching';ops.write_json(journal,tx)
    print('数据库迁移 3/3：切换挂载并配置开机挂载依赖',flush=True)
    install_mount(ops,root,dest)
    tx['fstabAfter']=FSTAB.read_text();tx['phase']='switched';ops.write_json(journal,tx)
    return p

def validate(ops,n,root):
    journal,_,_=paths(root);tx=json.loads(journal.read_text())
    if tx['phase']!='switched' or not os.path.samefile(dbpath(root),tx['plan']['target']):
        raise RuntimeError('迁移尚未完成挂载')
    # Open only MySQL while checking; HTTP and judge containers remain stopped.
    ops.run(['docker','start',n['site']+'db'])
    for _ in range(60):
        result=ops.run(['docker','exec',n['site']+'db','sh','-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N -e "SELECT 1"'],check=False,timeout=15)
        if result=='1': return dict(ready=True)
        time.sleep(1)
    raise RuntimeError('新目录 MySQL 启动或 SQL 验证未通过')

def finish(ops,n,root,rollback=False):
    journal,dropin,tag=paths(root)
    if not journal.exists(): return dict(pending=False)
    tx=json.loads(journal.read_text());src=dbpath(root);p=tx['plan']
    if rollback:
        # Never roll back after opening the application to writes.
        if any(x['running'] for x in ops.inspect_containers(n)): raise RuntimeError('恢复挂载前必须停止全部 Web 容器')
        if tx['phase'] in ('switching','switched'):
            if 'fstabAfter' in tx and FSTAB.read_text()!=tx['fstabAfter']: raise RuntimeError('迁移后 fstab 已变化，请人工检查，避免覆盖其他挂载')
            if is_mount(src): ops.run(['umount',str(src)])
            atomic_text(FSTAB,tx['fstabBefore'])
            if tx['dropinBefore'] is None: dropin.unlink(missing_ok=True)
            else: atomic_text(dropin,tx['dropinBefore'])
            ops.run(['systemctl','daemon-reload'])
            if p['previous']: ops.run(['mount','--bind',p['previous']['target'],str(src)])
        phase='rolled-back'
    else:
        if tx['phase']!='switched' or not os.path.samefile(src,p['target']): raise RuntimeError('挂载未切换，不能完成')
        ops.write_json(root/'private/database-storage.json',dict(target=p['target'],officialPath=str(src),created=time.time()))
        phase='committed'
    tx['phase']=phase
    ops.write_json(root/'private'/('database-migration-'+uuid.uuid4().hex+'.json'),tx)
    journal.unlink()
    return dict(phase=phase,target=p['target'],originalRetained=True)

def initialize(ops,n,root):
    target=n.get('databasePath','')
    if not target:return
    state=root/'private/database-storage.json'
    if state.exists():
        old=json.loads(state.read_text())
        if old['target']!=target or not os.path.samefile(dbpath(root),target):
            raise RuntimeError('数据库目录配置与实际挂载不符；请检查存储状态')
        return
    begin(ops,n,root,dict(target=target),True)
    finish(ops,n,root)

def assert_ready(root):
    if (root/'private/database-migration.json').exists():
        raise RuntimeError('数据库迁移未完成，请先恢复；禁止启动或更新业务')
    p=root/'private/database-storage.json'
    if p.exists():
        dest=json.loads(p.read_text())['target']
        if not is_mount(dbpath(root)) or not Path(dest).exists() or not os.path.samefile(dbpath(root),dest):
            raise RuntimeError('配置的数据库磁盘未正确挂载，禁止使用系统盘上的旧目录启动')
