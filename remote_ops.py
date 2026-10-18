"""Uploaded to Ubuntu; standard library only. Narrow, journaled host operations."""
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import pwd
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import database_storage
from urllib.parse import quote

def saved_image_config_matches(ref, expected, actual):
    """Containerd may retain a parent index ID after loading a platform archive."""
    p = subprocess.Popen(['docker', 'image', 'save', ref], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(180, p.kill); timer.start()
    configs = {}; manifest = None
    try:
        with tarfile.open(fileobj=p.stdout, mode='r|') as tar:
            for member in tar:
                name = member.name.removeprefix('./')
                if not member.isfile() or member.size > 4*1024**2: continue
                if name == 'manifest.json':
                    manifest = json.load(tar.extractfile(member))
                elif name.endswith('.json') or name.startswith('blobs/sha256/'):
                    raw = tar.extractfile(member).read()
                    if 'sha256:'+hashlib.sha256(raw).hexdigest() == expected:
                        configs[name] = json.loads(raw)
        if p.wait(timeout=10) != 0: return False
        for entry in manifest or []:
            if ref not in (entry.get('RepoTags') or []): continue
            config = configs.get(entry.get('Config'))
            if config and config.get('os') == 'linux' and config.get('architecture') == 'amd64':
                return config.get('rootfs',{}).get('diff_ids') == actual.get('RootFS',{}).get('Layers')
        return False
    finally:
        timer.cancel()
        if p.poll() is None: p.kill()
        p.wait(); p.stdout.close()

def run(args, check=True, timeout=7200, input=None):
    p = subprocess.run(args, text=True, input=input, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if p.returncode and check: raise RuntimeError('命令失败 ' + args[0] + ': ' + p.stdout[-4000:])
    return p.stdout.strip()

def write_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.new'); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)); tmp.chmod(0o600); tmp.replace(p)

def names(n):
    if n['kind'] == 'web':
        s = n['site']; return ['php-' + s, s + 'nginx-server', s + 'myadmin', s + 'db']
    base = 'judge-' + n['id']
    return [base] + [base + '-' + str(i) for i in range(1, n['pods'])]

def images(n, c):
    if n['kind'] == 'judge': return ['csgrandeur/ccpcoj-judge2:' + c['version']]
    return ['csgrandeur/ccpcoj-web2:' + c['version'], 'mysql:8.0.32', 'nginx:1.29.7-alpine', 'phpmyadmin:5.2.3']

def docker_exists(): return shutil.which('docker') is not None

def inspect_containers(n):
    result = []
    if not docker_exists(): return result
    for name in names(n):
        p = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
        if p.returncode == 0:
            x = json.loads(p.stdout)[0]
            result.append(dict(name=name, running=x['State']['Running'], image=x['Config']['Image'], imageId=x['Image'], status=x['State']['Status'], mounts=x['Mounts']))
    return result

def validate_mounts(n, root):
    for x in inspect_containers(n):
        if not any(str(v.get('Source', '')).startswith(str(root / 'data') + '/') for v in x['mounts']):
            # Official phpMyAdmin is stateless and has no project volume. Verify its DB binding
            # against the project's mounted MySQL instead of requiring a nonexistent mount.
            if n['kind']=='web' and x['name']==n['site']+'myadmin' and not x['mounts']:
                admin=json.loads(run(['docker','inspect',x['name']]))[0]
                db=json.loads(run(['docker','inspect',n['site']+'db']))[0]
                env=dict(v.split('=',1) for v in admin['Config'].get('Env') or [])
                if (env.get('PMA_HOST')==n['site']+'db' and admin['Config']['Image'].startswith('phpmyadmin:')
                    and set(admin['NetworkSettings']['Networks']) & set(db['NetworkSettings']['Networks'])
                    and any(v.get('Source','').startswith(str(root/'data')+'/') for v in db['Mounts'])):
                    continue
            raise RuntimeError('同名容器不属于项目目录：' + x['name'])

def owned(n, root):
    marker = root / '.csg-console.json'
    if not marker.exists(): raise RuntimeError('该目录不属于本工具管理；请先检查并接管已有部署')
    m = json.loads(marker.read_text())
    if m['id'] != n['id'] or m['kind'] != n['kind']: raise RuntimeError('项目标记不匹配')
    validate_mounts(n, root)

def adoption_inspect(n, root):
    check_os()
    if n['role'] != 'active': raise RuntimeError('接管仅用于已运行的启用节点')
    if root.resolve() != root or not (root/'data').is_dir(): raise RuntimeError('接管目录不正确或包含符号链接')
    marker=root/'.csg-console.json'
    if marker.exists(): owned(n,root)
    validate_mounts(n,root)
    full=[json.loads(run(['docker','inspect',name]))[0] for name in names(n)]
    if not all(x['State']['Running'] for x in full): raise RuntimeError('先恢复原服务，再接管完整运行实例')
    env=lambda x:dict(v.split('=',1) for v in x['Config'].get('Env',[]) if '=' in v)
    apps=[x for x in full if n['kind']=='judge' or x['Name']=='/php-'+n['site']]
    prefix='csgrandeur/ccpcoj-'+('judge2' if n['kind']=='judge' else 'web2')+':'
    versions={x['Config']['Image'].removeprefix(prefix) for x in apps if x['Config']['Image'].startswith(prefix)}
    if len(versions)!=1 or any(not x['Config']['Image'].startswith(prefix) for x in apps):
        raise RuntimeError('无法确定原官方应用版本；拒绝接管')
    version=next(iter(versions))
    if not re.fullmatch(r'[0-9][A-Za-z0-9_.-]{0,63}',version): raise RuntimeError('原镜像版本格式不受支持')
    if n['kind']=='web':
        byname={x['Name'].lstrip('/'):x for x in full};s=n['site'];app=env(apps[0]);db=env(byname[s+'db'])
        if app.get('DB_HOSTNAME')!=s+'db' or app.get('DB_DATABASE')!='csgcpc_'+s or app.get('OJ_NAME')!=s:
            raise RuntimeError('站点标识或数据库绑定与现有实例不符')
        if db.get('MYSQL_ROOT_PASSWORD')!=n['secrets']['sqlRoot'] or app.get('DB_PASSWORD')!=n['secrets']['sqlUser']:
            raise RuntimeError('填写的原数据库口令与现有实例不符；不会改写口令')
        ports={int(v['HostPort']) for values in byname[s+'nginx-server']['HostConfig'].get('PortBindings',{}).values() for v in values or []}
        if not {n['port'],n['myadminPort']}<=ports: raise RuntimeError('Web 或 phpMyAdmin 端口与现有实例不符')
        data=root/'data'
        for x in full:
            for m in x['Mounts']:
                src=Path(m['Source'])
                if m['Type']!='bind' or (src!=Path('/etc/localtime') and (not src.is_relative_to(data) or src.resolve()!=src)):
                    raise RuntimeError('Web 有未覆盖到冷备份的挂载，需先核对：'+x['Name'])
    else:
        base='judge-'+n['id'];matching=[v for v in run(['docker','ps','-a','--format','{{.Names}}']).splitlines() if v==base or v.startswith(base+'-')]
        if set(matching)!=set(names(n)): raise RuntimeError('实际评测容器数与 pods 配置不一致')
        for x in apps:
            e=env(x)
            if e.get('CSGOJ_SERVER_USERNAME')!=n['judgeUser'] or e.get('CSGOJ_SERVER_PASSWORD')!=n['judgePassword']:
                raise RuntimeError('原评测账号或口令不匹配；不会改写账号')
            if e.get('CSGOJ_SERVER_BASE_URL','').rstrip('/')!=n['webUrl'].rstrip('/'):
                raise RuntimeError('现有评测机绑定的 Web 地址不匹配')
    tool=root/'csgoj-deploy'
    if not tool.is_file(): raise RuntimeError('项目目录缺少原部署程序')
    # Exclude counters, live IPs and timestamps; pin identity and immutable runtime configuration.
    identity=[{k:x[k] for k in ('Id','Image','Config','HostConfig','Mounts')} for x in full]
    for x in identity:x['Mounts']=sorted(x['Mounts'],key=lambda m:m['Destination'])
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return dict(version=version,fingerprint=fingerprint,containers=[dict(name=x['Name'].lstrip('/'),id=x['Id'],image=x['Config']['Image'],imageId=x['Image']) for x in full],toolSha256=file_hash(tool))

def adopt(n, c, root, extra):
    result=adoption_inspect(n,root)
    if result['fingerprint']!=extra.get('fingerprint'): raise RuntimeError('检查后容器发生变化，请重新检查接管')
    marker=root/'.csg-console.json'
    if marker.exists(): return dict(**result,adopted=True)
    # Write audit metadata before ownership; do not stop containers, tune host or initialize SQL.
    write_json(root/'private/adoption-baseline.json',result)
    persisted={k:v for k,v in n.items() if k not in ('password','elevatePassword')}
    write_json(root/'private/managed-node.json',dict(node=persisted,version=result['version'],adopted=True))
    with marker.open('x') as f:json.dump(dict(id=n['id'],kind=n['kind'],created=time.time(),adopted=True),f)
    marker.chmod(0o644)  # Identity only; readable by SSH user for repeat inspection after interrupted local save.
    return dict(**result,adopted=True)

def check_os():
    text = Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in text or 'VERSION_ID="24.04"' not in text or os.uname().machine != 'x86_64':
        raise RuntimeError('本工具仅验证 Ubuntu 24.04 LTS x86_64；当前系统不匹配')

@contextlib.contextmanager
def mirror_scope(mirror, root):
    if not mirror: yield; return
    path = Path('/etc/docker/daemon.json')
    original = path.read_bytes() if path.exists() else None
    previous = json.loads(original or b'{}')
    active = dict(previous); active['registry-mirrors'] = [mirror]
    write_json(root / 'private' / 'docker-daemon-before.json', previous)
    try:
        write_json(path, active)
        run(['dockerd', '--validate', '--config-file', str(path)])
        run(['systemctl', 'reload', 'docker'])
        yield
    finally:
        # Docker 29 retains omitted mirrors on reload, so explicitly clear first.
        restored = dict(previous); restored.setdefault('registry-mirrors', [])
        write_json(path, restored)
        run(['systemctl', 'reload', 'docker'])
        if original is None: path.unlink()
        else: path.write_bytes(original)
        print('临时 Docker 镜像配置已恢复', flush=True)

def tune(n, c, root):
    p = root / 'private' / 'host-settings.json'
    old = json.loads(p.read_text()) if p.exists() else {}
    if c.get('neverSleep'):
        targets = ['sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target', 'suspend-then-hibernate.target']
        states = old.setdefault('sleepTargets', {})
        for target in targets:
            if target not in states:
                states[target] = run(['systemctl', 'is-enabled', target], check=False).splitlines()[0]
                write_json(p, old)
        # Mask blocks automatic idle sleep and explicit sleep requests; no reboot/logind restart needed.
        run(['systemctl', 'mask'] + targets)
        for target in targets:
            if run(['systemctl', 'is-enabled', target], check=False) != 'masked':
                raise RuntimeError('未能禁用休眠目标：' + target)
        print('永不休眠：sleep / suspend / hibernate 等系统目标已禁用', flush=True)
    if c.get('disableGui'):
        if 'target' not in old:
            old['target'] = run(['systemctl', 'get-default'])
            old['displayActive'] = run(['systemctl', 'is-active', 'display-manager'], check=False) == 'active'
            write_json(p, old)
        run(['systemctl', 'set-default', 'multi-user.target'])
        run(['systemctl', 'stop', 'display-manager'], check=False)
        print('已关闭图形登录；SSH 网络服务保留', flush=True)
    if c.get('performance'):
        if shutil.which('powerprofilesctl') and 'performance' in run(['powerprofilesctl', 'list'], check=False):
            if 'profile' not in old:
                old['profile'] = run(['powerprofilesctl', 'get']); write_json(p, old)
            run(['powerprofilesctl', 'set', 'performance'])
            print('电源配置：performance', flush=True)
        else:
            governors = list(Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor'))
            changed = 0
            for g in governors:
                if 'performance' not in (g.parent / 'scaling_available_governors').read_text(): continue
                old.setdefault('governors', {}).setdefault(str(g), g.read_text().strip()); write_json(p, old)
                g.write_text('performance'); changed += 1
            print('已设置 CPU performance governor（重启后需重新应用）' if changed else '性能模式：硬件/虚拟机未提供，已跳过', flush=True)

def prepare(n, c, root, tool):
    check_os()
    if root.is_symlink() or root.resolve() != root: raise RuntimeError('项目目录或上级目录含符号链接，停止')
    root.mkdir(parents=True, exist_ok=True)
    marker = root / '.csg-console.json'
    if not marker.exists():
        if (root / 'data').exists() and any((root / 'data').iterdir()): raise RuntimeError('检测到现有数据；拒绝作为全新节点部署')
        if inspect_containers(n): raise RuntimeError('检测到同名既有容器；拒绝接管')
        write_json(marker, dict(id=n['id'], kind=n['kind'], created=time.time()))
    owned(n, root)
    (root / 'private').mkdir(exist_ok=True); (root / 'private').chmod(0o700)
    if not docker_exists():
        print('安装 Docker（apt 软件源需要联网）', flush=True)
        run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'update'])
        run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', 'docker.io', 'ca-certificates', 'curl'])
    run(['systemctl', 'enable', '--now', 'docker'])
    if not n.get('localWSL'): run(['systemctl', 'enable', 'ssh'])
    run(['docker', 'info'])
    if n['kind'] == 'judge':
        cpu = int(run(['docker', 'info', '--format', '{{.NCPU}}']))
        mem = int(run(['docker', 'info', '--format', '{{.MemTotal}}']))
        if cpu < 2 + 4 * n['pods'] or mem < (4 * n['pods'] + 1) * 1024**3:
            raise RuntimeError('资源不足：官方工具每 pod 需 4 核 4 GB，另预留 2 核和系统内存；请降低 pods')
    data = Path(tool).read_bytes()
    if hashlib.sha256(data).hexdigest() != c['tool']['sha256']: raise RuntimeError('部署程序 SHA256 不一致')
    # Guard the dependency profile against a changed official tool.
    if n['kind'] == 'web' and not all(ref.encode() in data for ref in images(n, c)[1:]):
        raise RuntimeError('此部署工具的依赖镜像与适配配置不同；请检查新工具源码后更新工具包')
    shutil.copyfile(tool, root / 'csgoj-deploy'); (root / 'csgoj-deploy').chmod(0o755)
    tune(n, c, root)
    for image in images(n, c):
        run(['docker', 'image', 'inspect', image])
        print('使用本机分发镜像 ' + image, flush=True)
    image = images(n, c)[0]
    program = 'g++ --version && python3 --version' if n['kind'] == 'judge' else 'php -v'
    run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/bin/sh', image, '-c', program], timeout=120)
    print('离线容器自检通过；镜像已缓存', flush=True)
    return dict(prepared=True, images=images(n, c))

def deploy(n, c, root):
    owned(n, root)
    if n['role'] != 'active': raise RuntimeError('备用节点禁止启动业务；请使用启用备用操作')
    if n['kind']=='web':
        database_storage.initialize(sys.modules[__name__],n,root)
        database_storage.assert_ready(root)
    tool = str(root / 'csgoj-deploy')
    help_text = run([tool, 'up' if n['kind']=='web' else 'judge', '--help'], check=False, timeout=30)
    if '-no-pull' not in help_text:
        raise RuntimeError('此部署程序不支持 --no-pull；请选用支持离线镜像的新版 Release，未执行部署')
    if n['kind'] == 'web':
        s = n['secrets']; site = n['site']
        args = [tool, 'up', '--noninteractive', '--path-data', str(root / 'data'), '--name-prefix', site,
                '--nginx-ranges', f"{n['port']}-{n['port']},{n['myadminPort']}-{n['myadminPort']}",
                '--site', f"{site}:{n['port']}:cpcsys:cpc", '--port-myadmin', str(n['myadminPort']), '--port-db', '0',
                '--sql-root', s['sqlRoot'], '--sql-user-pass', s['sqlUser'], '--myadmin-pass', s['myadmin'],
                '--no-redis', '--no-dev', '--no-build', '--csgoj-version', c['version']]
    else:
        args = [tool, 'judge', '--server-url', n['webUrl'], '--username', n['judgeUser'], '--password', n['judgePassword'],
                '--oj-name', n['id'], '--pods', str(n['pods']), '--path-data', str(root / 'data'), '--csgoj-version', c['version']]
    args.append('--no-pull')
    persisted = {k: v for k, v in n.items() if k not in ('password', 'elevatePassword')}
    write_json(root / 'private' / 'managed-node.json', dict(node=persisted, version=c['version']))
    os.umask(0o022)
    print(run(args), flush=True)
    actual = inspect_containers(n)
    if len(actual) != len(names(n)) or not all(x['running'] for x in actual): raise RuntimeError('容器未全部运行')
    return dict(containers=[{k: v for k, v in x.items() if k != 'mounts'} for x in actual])

def control(n, root, action):
    owned(n, root)
    if action == 'start':
        if n['role'] != 'active': raise RuntimeError('备用节点不能直接启动服务，请先执行启用流程')
        if n['kind']=='web': database_storage.assert_ready(root)
        # A stopped daemon is separate from stopped application containers.
        run(['systemctl', 'start', 'docker'])
        owned(n, root)
    actual = inspect_containers(n)
    if action == 'stop':
        running = [x['name'] for x in actual if x['running']]
        if running: run(['docker', 'stop', '--time', '120'] + running)
        return dict(stopped=running)
    if action == 'start':
        # DB first for Web startup; caller verifies HTTP and account login.
        existing = {x['name'] for x in actual}
        missing = set(names(n)) - existing
        if missing: raise RuntimeError('容器缺失，请重新部署所选节点：' + ', '.join(sorted(missing)))
        order = [n['site']+'db', n['site']+'myadmin', 'php-'+n['site'], n['site']+'nginx-server'] if n['kind']=='web' else names(n)
        for name in order:
            if not next(x['running'] for x in actual if x['name'] == name): run(['docker', 'start', name])
        actual = inspect_containers(n)
        if len(actual) != len(names(n)) or not all(x['running'] for x in actual): raise RuntimeError('启动后容器未全部运行，请检查容器日志')
    elif action == 'removeJudge':
        if n['kind'] != 'judge': raise RuntimeError('仅允许重建托管评测容器')
        control(n, root, 'stop')
        run([str(root / 'csgoj-deploy'), 'judge', '--oj-name', n['id'], '--remove-all'])
    return {}

class DockerSocket(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')

def docker_api(method, path, body=None):
    conn = DockerSocket('localhost', timeout=120)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, {'Content-Type':'application/json'})
        r = conn.getresponse(); raw = r.read()
        data = json.loads(raw) if raw else {}
        if r.status >= 300: raise RuntimeError('Docker API: '+str(data.get('message', r.status)))
        return data
    finally: conn.close()

def recreate(saved, image_id, updating=False):
    """Recreate only the application container using its existing runtime settings."""
    body = dict(saved['Config']); body['Image'] = image_id
    # Use new image defaults except where deployment explicitly overrode them.
    if updating:
        old = json.loads(run(['docker','image','inspect',saved['Image']]))[0]['Config']
        new = json.loads(run(['docker','image','inspect',image_id]))[0]['Config']
        for key in ('Entrypoint','Cmd','WorkingDir','User','Healthcheck'):
            if body.get(key) == old.get(key):
                if key in new: body[key] = new[key]
                else: body.pop(key,None)
        defaults = dict(x.split('=',1) for x in old.get('Env') or [])
        env = dict(x.split('=',1) for x in new.get('Env') or [])
        env.update({k:v for k,v in (x.split('=',1) for x in body.get('Env') or []) if defaults.get(k)!=v})
        # Runtime DB and site credentials always take precedence, even if matching a default.
        env.update({k:v for k,v in (x.split('=',1) for x in body.get('Env') or []) if k.startswith(('DB_','OJ_','CSGOJ_SERVER_','SESSION_','BELONG_','PORT_'))})
        if 'OJ_NAME' in env: env['OJ_UPDATE_STATIC']='1'
        body['Env'] = [k+'='+v for k,v in env.items()]
    if body.get('Hostname') == saved['Id'][:12]: body['Hostname']=''
    body['HostConfig'] = saved['HostConfig']
    endpoints = {}
    for name, info in saved['NetworkSettings']['Networks'].items():
        endpoints[name] = {k:info[k] for k in ('IPAMConfig','Links','DriverOpts') if info.get(k)}
        aliases = [a for a in info.get('Aliases') or [] if a not in (saved['Id'],saved['Id'][:12])]
        if aliases: endpoints[name]['Aliases']=aliases
    body['NetworkingConfig'] = {'EndpointsConfig':endpoints}
    version = docker_api('GET','/version')['ApiVersion']
    return docker_api('POST','/v'+version+'/containers/create?name='+quote(saved['Name'].lstrip('/')),body)['Id']

def update_check(n, c, root):
    owned(n,root)
    if (root/'private/update-transaction.json').exists(): raise RuntimeError('上一次更新尚未收尾；请先恢复未完成更新')
    image = images(n,c)[0]
    print('使用 Windows 本地归档分发的目标镜像：'+image,flush=True)
    target = json.loads(run(['docker','image','inspect',image]))[0]
    program = 'g++ --version && python3 --version' if n['kind']=='judge' else 'php -v'
    run(['docker','run','--rm','--network','none','--entrypoint','/bin/sh',target['Id'],'-c',program],timeout=120)
    apps = [x for x in inspect_containers(n) if n['kind']=='judge' or x['name']=='php-'+n['site']]
    return dict(targetVersion=c['version'], targetImage=image, targetId=target['Id'], digests=target.get('RepoDigests',[]),
                currentImages=[dict(name=x['name'],image=x['image'],imageId=x['imageId']) for x in apps],
                updateAvailable=any(x['imageId']!=target['Id'] for x in apps) if apps else True)

def version_inventory(n, root):
    """Inspect runtime and local Docker tags without starting or pulling anything."""
    if root.is_symlink() or root.resolve() != root: raise RuntimeError('项目目录包含符号链接')
    marker = root / '.csg-console.json'
    has_data = (root/'data').exists() and any((root/'data').iterdir())
    has_config = any((root/f).exists() for f in ('private/managed-node.json', 'docker-compose.yml', 'compose.yml', '.env'))
    daemon = docker_exists() and subprocess.run(['docker','info'],capture_output=True,timeout=15).returncode == 0
    if docker_exists() and not daemon: raise RuntimeError('Docker 未运行，无法判断现有容器；请先启动 Docker 再同步')
    current = inspect_containers(n) if daemon else []
    prefix = 'csgrandeur/ccpcoj-'+('web2' if n['kind']=='web' else 'judge2')+':'
    rows = []
    if daemon:
        tags = run(['docker','image','ls','--format','{{.Repository}}:{{.Tag}}']).splitlines()
        for ref in sorted(set(tags)):
            if not (ref.startswith(prefix) or ref in images(n,dict(version='unused'))[1:]): continue
            if ref.endswith(':<none>'): continue
            item = json.loads(run(['docker','image','inspect',ref]))[0]
            if item.get('Os')=='linux' and item.get('Architecture')=='amd64':
                rows.append(dict(ref=ref, version=ref.rsplit(':',1)[1], imageId=item['Id']))
    managed = marker.exists()
    if managed: owned(n,root)
    if (root/'private/update-transaction.json').exists():
        mode,reason='blocked','存在未完成更新，请先恢复未完成更新'
    elif current:
        if len(current)!=len(names(n)): mode,reason='blocked','原容器不完整，请先恢复；不会重新初始化'
        else: mode,reason=('update' if managed else 'adoptUpdate'),'保留原配置更新'+('' if managed else '（自动核验接管）')
    elif has_data or has_config:
        mode,reason='blocked','发现旧配置或数据但没有完整容器，需先恢复；不会重新初始化'
    else: mode,reason='initialize','未发现旧业务配置，首次初始化'
    apps=[x for x in current if n['kind']=='judge' or x['name']=='php-'+n['site']]
    recorded={}
    if managed and (root/'private/managed-node.json').exists(): recorded=json.loads((root/'private/managed-node.json').read_text())
    versions=set()
    for app in apps:
        if app['image'].startswith(prefix): versions.add(app['image'].removeprefix(prefix))
        else:
            matches=[r['version'] for r in rows if r['ref'].startswith(prefix) and r['imageId']==app['imageId']]
            versions.update(matches or ([recorded['version']] if recorded.get('version') else ['未知标签']))
    return dict(mode=mode,reason=reason,managed=managed,currentVersions=sorted(versions),
                containers=[{k:v for k,v in x.items() if k!='mounts'} for x in current],
                images=rows,availableVersions=sorted({r['version'] for r in rows if r['ref'].startswith(prefix)}),
                checkedAt=time.time())

def update_begin(n, c, root, extra):
    owned(n,root)
    journal = root/'private/update-transaction.json'
    if journal.exists(): raise RuntimeError('存在未完成的更新，请先恢复')
    actual = inspect_containers(n)
    if len(actual)!=len(names(n)) or any(x['running'] for x in actual): raise RuntimeError('更新要求完整的原容器已停止')
    target = extra['targetId']; run(['docker','image','inspect',target])
    full = [json.loads(run(['docker','inspect',name]))[0] for name in names(n)]
    apps = [x for x in full if n['kind']=='judge' or x['Name']=='/php-'+n['site']]
    if any(m['Type']!='bind' for x in apps for m in x['Mounts']): raise RuntimeError('应用容器存在非 bind 挂载，需人工核对更新方式')
    tx = dict(version=c['version'], previousVersion=n.get('deployedVersion'), containers=full, apps=apps, targetId=target, phase='prepared')
    if n['kind']=='web':
        size = int(run(['du','-sb',str(root/'data')]).split()[0])
        if shutil.disk_usage(root).free < size*2+100*1024**2: raise RuntimeError('更新备份及恢复空间不足')
        archive = root/'private'/('update-backup-'+uuid.uuid4().hex+'.tar.gz')
        run(['tar','--numeric-owner','-czpf',str(archive),'-C',str(root),'data'])
        archive.chmod(0o600)
        tx.update(archive=str(archive),sha256=file_hash(archive))
    write_json(journal,tx)
    for x in apps:
        run(['docker','rm',x['Id']]); recreate(x,target,updating=True)
    tx['phase']='replaced'; write_json(journal,tx)
    return dict(replaced=[x['Name'].lstrip('/') for x in apps], targetId=target)

def update_finish(n,c,root,rollback=False):
    owned(n,root); journal=root/'private/update-transaction.json'
    if not journal.exists(): return dict(transaction=False)
    tx=json.loads(journal.read_text())
    if rollback:
        control(n,root,'stop')
        if tx.get('archive') and not tx.get('dataRestored'):
            archive=Path(tx['archive'])
            if archive.parent!=root/'private' or file_hash(archive)!=tx['sha256']: raise RuntimeError('更新备份校验失败，保持停止')
            data=root/'data'; failed=root/('data.failed-'+uuid.uuid4().hex[:12])
            if (root/'private/database-storage.json').exists():
                database_storage.assert_ready(root)
                if not shutil.which('rsync'): raise RuntimeError('保留数据库挂载的恢复需要 rsync')
                failed.mkdir()
                run(['tar','--numeric-owner','-czpf',str(failed/'data-before-restore.tar.gz'),'-C',str(root),'data'])
                staging=root/('restore-stage-'+uuid.uuid4().hex)
                staging.mkdir()
                run(['tar','--numeric-owner','-xzpf',str(archive),'-C',str(staging)])
                run(['rsync','-aHAX','--numeric-ids','--delete',str(staging/'data')+'/',str(data)+'/'])
            else:
                if data.exists(): data.rename(failed)
                run(['tar','--numeric-owner','-xzpf',str(archive),'-C',str(root)])
            tx['dataRestored']=True; write_json(journal,tx)
            print('失败版本的数据保留在 '+str(failed),flush=True)
        for x in tx['apps']:
            run(['docker','rm','-f',x['Name'].lstrip('/')],check=False)
            recreate(x,x['Image'])
        tx['phase']='rolled-back'
    else:
        current={x['name']:x for x in inspect_containers(n)}
        for x in tx['apps']:
            now=current.get(x['Name'].lstrip('/'),{})
            if now.get('imageId')!=tx['targetId'] or not now.get('running'): raise RuntimeError('目标镜像未全部运行，拒绝完成更新')
        for x in tx['containers']:
            if x in tx['apps']: continue
            now=json.loads(run(['docker','inspect',x['Name']]))[0]
            mounts=lambda item:sorted(item['Mounts'],key=lambda m:m['Destination'])
            if now['Id']!=x['Id'] or sorted(now['Config']['Env'])!=sorted(x['Config']['Env']) or mounts(now)!=mounts(x):
                raise RuntimeError('基础服务或配置意外变化，拒绝完成更新')
        managed=root/'private/managed-node.json'
        if managed.exists():
            data=json.loads(managed.read_text());data['version']=c['version'];write_json(managed,data)
        tx['phase']='committed'
    write_json(root/'private'/('update-'+uuid.uuid4().hex+'.json'),tx)
    journal.unlink()
    return dict(transaction=True,phase=tx['phase'],backup=tx.get('archive'),previousVersion=tx.get('previousVersion'))

def snapshot(n, root, extra):
    owned(n, root)
    if any(x['running'] for x in inspect_containers(n)): raise RuntimeError('冷备份要求所有站点容器已停止')
    if n['kind'] != 'web' or not (root / 'data' / 'var/mysql/mysql_data').exists(): raise RuntimeError('没有可备份的 Web 数据')
    for x in inspect_containers(n):
        if x['name'].endswith('db'):
            status = json.loads(run(['docker', 'inspect', x['name']]))[0]['State']
            if status['ExitCode'] != 0: raise RuntimeError('MySQL 未正常退出，拒绝冷快照')
    account = pwd.getpwnam(extra['user'])
    dest = Path(account.pw_dir) / ('.csg-snapshot-' + uuid.uuid4().hex + '.tar.gz')
    run(['tar', '--numeric-owner', '-czpf', str(dest), '-C', str(root), 'data'])
    dest.chmod(0o600)
    os.chown(dest, account.pw_uid, account.pw_gid)
    return dict(path=str(dest), sha256=file_hash(dest), bytes=dest.stat().st_size)

def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''): h.update(part)
    return h.hexdigest()

def restore(n, root, extra):
    owned(n, root)
    if any(x['running'] for x in inspect_containers(n)): raise RuntimeError('目标存在运行中的服务，拒绝覆盖')
    data = root / 'data'
    if data.exists() and any(data.iterdir()): raise RuntimeError('目标目录已有数据，请换一个空项目目录；本工具不会覆盖')
    archive = Path(extra['archive'])
    if file_hash(archive) != extra['sha256']: raise RuntimeError('备份 SHA256 不符')
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] != 'data' or '..' in parts or member.isdev(): raise RuntimeError('不安全的归档路径')
            if member.issym() or member.islnk():
                if os.path.isabs(member.linkname) or '..' in Path(member.linkname).parts: raise RuntimeError('归档包含外部链接，需人工审查')
    if n.get('kind')=='web': database_storage.initialize(sys.modules[__name__],n,root)
    run(['tar', '--numeric-owner', '-xzpf', str(archive), '-C', str(root)])
    return dict(restored=True)

def main(job):
    n, c, action = job['node'], job['config'], job['action']; root = Path(n['path'])
    ops=sys.modules[__name__]
    if action=='databaseInspect': return database_storage.inspect(ops,n,root)
    if n.get('kind')=='web': database_storage.assert_project(ops,n,root)
    if action=='databaseCheck': return database_storage.plan(ops,n,root,job['extra']['target'])
    if action=='databaseBegin': return database_storage.begin(ops,n,root,job['extra'])
    if action=='databaseValidate': return database_storage.validate(ops,n,root)
    if action in ('databaseCommit','databaseRollback'):
        return database_storage.finish(ops,n,root,action=='databaseRollback')
    if n.get('kind')=='web' and action in ('updateCheck','updateBegin','snapshot','adopt'):
        database_storage.assert_ready(root)
    if action == 'imagePrerequisite':
        check_os()
        home = pwd.getpwnam(n['user']).pw_dir
        if shutil.disk_usage(home).free < int(job['extra'].get('uploadBytes',0)) + 64*1024**2:
            raise RuntimeError('SSH 用户目录空间不足，无法接收镜像归档')
        if not docker_exists():
            print('安装 Docker（仅系统软件包需要访问 apt；镜像从 Windows 分发）', flush=True)
            run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'update'])
            run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', 'docker.io', 'ca-certificates', 'curl'])
        run(['systemctl', 'enable', '--now', 'docker'])
        return dict(ready=True)
    if action == 'loadImage':
        extra = job['extra']; archive = Path(extra['archive'])
        print('校验上传镜像 SHA256…', flush=True)
        if file_hash(archive) != extra['sha256']: raise RuntimeError('镜像文件 SHA256 不一致，拒绝加载')
        run(['docker', 'info'])
        print('正在 docker load（解压可能需要数分钟）…', flush=True)
        print(run(['docker', 'load', '-i', str(archive)]), flush=True)
        for ref, expected in extra['images'].items():
            actual = json.loads(run(['docker', 'image', 'inspect', ref]))[0]
            valid_ids = extra.get('identities',{}).get(ref,[expected])
            platform_ok = actual['Os'] == 'linux' and actual['Architecture'] == 'amd64'
            identity_ok = actual['Id'] in valid_ids
            if platform_ok and not identity_ok:
                print('镜像返回索引 ID，正在核对实际配置摘要和全部文件系统层：'+ref, flush=True)
                identity_ok = saved_image_config_matches(ref, expected, actual)
            if not platform_ok or not identity_ok:
                raise RuntimeError('加载后的镜像内容或平台不符：'+ref)
        return dict(loaded=extra['images'])
    if action == 'adoptInspect': return adoption_inspect(n,root)
    if action == 'versionInventory': return version_inventory(n,root)
    if action == 'adopt': return adopt(n,c,root,job['extra'])
    if action == 'updateCheck': return update_check(n,c,root)
    if action == 'updateBegin': return update_begin(n,c,root,job['extra'])
    if action in ('updateCommit','updateRollback'): return update_finish(n,c,root,action=='updateRollback')
    if action == 'health':
        daemon = docker_exists() and subprocess.run(['docker', 'info'], capture_output=True, timeout=15).returncode == 0
        cached = {}
        for im in images(n, c):
            if daemon:
                p = subprocess.run(['docker', 'image', 'inspect', im], capture_output=True, text=True)
                if p.returncode == 0: cached[im] = json.loads(p.stdout)[0]['Id']
        containers=[{k: v for k, v in x.items() if k != 'mounts'} for x in inspect_containers(n)] if daemon else []
        pods={}
        if daemon and n['kind']=='judge':
            base='judge-'+n['id']
            matching=[v for v in run(['docker','ps','-a','--format','{{.Names}}']).splitlines()
                      if v==base or re.fullmatch(re.escape(base)+r'-\d+',v)]
            actual=[json.loads(run(['docker','inspect',name]))[0] for name in matching]
            pods=dict(configuredPods=n['pods'],runningPods=sum(bool(x['State']['Running']) for x in actual),
                      totalPods=len(actual),podNames=matching)
        return dict(online=True, dockerRunning=bool(daemon), os=run(['uname', '-sr']), cpu=os.cpu_count(), containers=containers, prepared=(root / '.csg-console.json').exists(), images=list(cached), imageIds=cached, **pods)
    if action == 'migrationCheck':
        owned(n, root)
        if job['extra'].get('source'):
            return dict(dataBytes=int(run(['du', '-sb', str(root / 'data')]).split()[0]), freeBytes=shutil.disk_usage(pwd.getpwnam(n['user']).pw_dir).free)
        if inspect_containers(n) or ((root / 'data').exists() and any((root / 'data').iterdir())):
            raise RuntimeError('迁移目标必须没有容器和业务数据')
        return dict(freeBytes=shutil.disk_usage(root).free)
    if action == 'judgeResizeCheck':
        owned(n, root)
        if n['kind'] != 'judge': raise RuntimeError('只能调整评测机 pods')
        pods = int(job['extra']['pods'])
        if not 1 <= pods <= 16: raise RuntimeError('目标 pods 必须在 1–16 之间')
        cpu = int(run(['docker','info','--format','{{.NCPU}}']))
        mem = int(run(['docker','info','--format','{{.MemTotal}}']))
        if cpu < 2 + 4 * pods or mem < (4 * pods + 1) * 1024**3:
            raise RuntimeError(f'资源不足：目标 {pods} pods 至少需 {2+4*pods} 核、{4*pods+1} GiB；当前 {cpu} 核、{mem/1024**3:.1f} GiB，未停止原容器')
        if not (root / 'csgoj-deploy').is_file(): raise RuntimeError('服务器缺少已准备的部署程序')
        run(['docker','image','inspect',images(n,c)[0]])
        base = 'judge-' + n['id']
        matching = [v for v in run(['docker','ps','-a','--format','{{.Names}}']).splitlines() if v == base or v.startswith(base+'-')]
        if set(matching) - set(names(n)): raise RuntimeError('发现配置数量之外的同名评测容器，需先核对，未停止原容器')
        return dict(cpu=cpu, memory=mem, targetPods=pods)
    if action == 'prepare': return prepare(n, c, root, job['tool'])
    if action == 'deploy': return deploy(n, c, root)
    if action in ('stop', 'start', 'removeJudge'): return control(n, root, action)
    if action == 'snapshot': return snapshot(n, root, job['extra'])
    if action == 'restore': return restore(n, root, job['extra'])
    if action == 'sql':
        owned(n, root)
        if n['kind'] != 'web': raise RuntimeError('SQL 仅适用于 Web')
        query = job['extra']['query']
        allowed = ('SELECT ', 'UPDATE solution SET result=0 WHERE result IN (2,3)')
        if not query.startswith(allowed): raise RuntimeError('不支持的内部 SQL 操作')
        out = run(['docker', 'exec', n['site'] + 'db', 'sh', '-c', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --default-character-set=utf8mb4 -N -uroot "$1" -e "$2"', 'sh', 'csgcpc_' + n['site'], query])
        return dict(value=out)
    if action == 'restoreHost':
        owned(n, root); p = root / 'private/host-settings.json'
        old = json.loads(p.read_text())
        if 'target' in old: run(['systemctl', 'set-default', old['target']])
        if old.get('displayActive'): run(['systemctl', 'start', 'display-manager'])
        if old.get('profile'): run(['powerprofilesctl', 'set', old['profile']])
        for target, state in old.get('sleepTargets', {}).items():
            if state not in ('masked', 'masked-runtime'):
                run(['systemctl', 'unmask', target])
            elif state == 'masked-runtime':
                run(['systemctl', 'unmask', target]); run(['systemctl', 'mask', '--runtime', target])
        for name, value in old.get('governors', {}).items():
            if Path(name).exists(): Path(name).write_text(value)
        return dict(restored=True)
    raise ValueError('未知操作')

if __name__ == '__main__':
    os.umask(0o077)
    job = json.loads(Path(sys.argv[1]).read_text())
    values = [job['node'].get(x, '') for x in ('password', 'elevatePassword', 'judgePassword')] + list(job['node'].get('secrets', {}).values())
    try:
        # Serialize all managed operations on a host, including daemon changes.
        if job['action']=='adoptInspect':
            result=main(job)  # Read-only Docker inspection can run as the existing SSH/docker user.
        else:
            with open('/run/lock/csgoj-console.lock', 'w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = main(job)
        print('CSGRESULT:' + json.dumps(result), flush=True)
    except Exception as exc:
        message = str(exc)
        for value in values:
            if value: message = message.replace(value, '[已隐藏]')
        print(message, flush=True); sys.exit(1)
