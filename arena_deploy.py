"""Local artifact cache and WSL Ansible orchestration for CSGArena."""
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import uuid
import sys
import network_policy as network
from network_relay import WSL_RELAY
from ssh_auth import normalize_auth, prepare_keys
from arena_ssh import inspect_hosts, accept_hosts, known_hosts_text

from storage import dpapi, restrict
from arena_payloads import LINUX_SCRIPT, WINDOWS_SCRIPT
from arena_release import DEFAULT_TAG, REPOSITORY, asset_url, default_artifacts, release_tag, authenticated_download

DEFAULT_PORT = 39721
KINDS = ('server-linux', 'server-windows', 'client-linux', 'client-windows')


def defaults():
    return dict(distro='Ubuntu-24.04', releaseTag=DEFAULT_TAG, servers=[], clients=[], artifacts=default_artifacts())


def integer(value, low, high, label):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise ValueError(label + '必须是整数')
    n = int(value)
    if not low <= n <= high:
        raise ValueError(f'{label}范围为 {low}–{high}')
    return n


def ipv4(value):
    try:
        addr = ipaddress.IPv4Address(str(value).strip())
        if addr.is_unspecified or addr.is_multicast or addr.is_loopback or int(addr) == 0xffffffff:
            raise ValueError()
        return str(addr)
    except ValueError:
        raise ValueError('请填写有效的目标 IPv4 地址') from None


def normalize(raw):
    c = defaults()
    c['releaseTag'] = release_tag(raw.get('releaseTag', DEFAULT_TAG))
    c['distro'] = str(raw.get('distro', c['distro'])).strip()
    if not re.fullmatch(r'[\w .-]{1,80}', c['distro']):
        raise ValueError('WSL 发行版名称无效')
    ids, addresses = set(), set()
    for group in ('servers', 'clients'):
        for original in raw.get(group, []):
            n = {k: str(original.get(k, '')).strip() for k in ('id', 'name', 'os', 'user', 'elevation', 'server')}
            if 'user' not in original: n['user'] = 'root'
            n.update({k: original.get(k, '') for k in ('privateKeyPath', 'keyPassphrase')})
            normalize_auth(n)
            for k in ('password', 'rootPassword'):
                n[k] = str(original.get(k, ''))
                if '\x00' in n[k]: raise ValueError('密码不能包含 NUL')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', n['id']) or n['id'] in ids:
                raise ValueError('条目标识为空或重复')
            ids.add(n['id'])
            if n['os'] not in ('linux', 'windows'): raise ValueError('请选择 Linux 或 Windows')
            if not n['user'] or '\n' in n['user']: raise ValueError('请填写登录用户名')
            port = original.get('port')
            n['port'] = integer((22 if n['os'] == 'linux' or n['privateKeyPath'] else 5986) if port in ('', None) else port, 1, 65535, '连接端口')
            n['elevation'] = n['elevation'] or 'sudo'
            if n['elevation'] not in ('sudo', 'su'): raise ValueError('提权方式无效')
            if group == 'servers':
                n['host'] = ipv4(original.get('host', ''))
                port = original.get('servicePort')
                n['servicePort'] = integer(DEFAULT_PORT if port in ('', None) else port, 1, 65534, '管控业务端口')
                n['scheme'] = original.get('scheme', 'https')
                if n['scheme'] not in ('http', 'https'): raise ValueError('业务协议无效')
                ips = [n['host']]
            else:
                prefix = original.get('prefix', [])
                if not isinstance(prefix, list) or len(prefix) != 3: raise ValueError('IP 段需填写前三段')
                n['prefix'] = [integer(v, 0, 255, 'IP 分段') for v in prefix]
                n['start'] = integer(original.get('start', ''), 1, 254, '起始尾段')
                n['end'] = integer(original.get('end', ''), 1, 254, '结束尾段')
                if n['end'] < n['start']: raise ValueError('IP 段结束值不能小于起始值')
                ips = [ipv4('.'.join(map(str, n['prefix'] + [i]))) for i in range(n['start'], n['end'] + 1)]
            for ip in ips:
                if ip in addresses: raise ValueError('目标 IP 重复或区间重叠：' + ip)
                addresses.add(ip)
            c[group].append(n)
    if len(addresses) > 4096: raise ValueError('单份配置最多 4096 台目标')
    servers = {n['id'] for n in c['servers']}
    if any(n['server'] and n['server'] not in servers for n in c['clients']): raise ValueError('选手端绑定的服务端不存在')
    for kind in KINDS:
        fallback = c['artifacts'][kind] if c['releaseTag'] == DEFAULT_TAG else dict(path='', url='', sha256='')
        a = raw.get('artifacts', {}).get(kind, fallback)
        a = {k: str(a.get(k, '')).strip() for k in ('path', 'url', 'sha256')}
        a['sha256'] = a['sha256'].lower()
        if a['sha256'] and not re.fullmatch('[0-9a-f]{64}', a['sha256']): raise ValueError('SHA256 必须是 64 位十六进制')
        if a['url']:
            u = urlsplit(a['url'])
            if u.scheme != 'https' or not u.hostname or u.username or u.password: raise ValueError('下载地址须为 HTTPS，不能在地址中包含登录凭据')
            if not a['path'] and a['url'].startswith(f'https://github.com/{REPOSITORY}/releases/download/') and a['url'] != asset_url(kind, c['releaseTag']):
                raise ValueError('版本号与官方产物地址不一致，请先读取并应用对应 Release')
        c['artifacts'][kind] = a
    return c


def targets(c, role, selected=None):
    if role not in ('server', 'client'): raise ValueError('部署类型无效')
    out = []
    servers = {s['id']: s for s in c['servers']}
    for n in c[role + 's']:
        ips = [n['host']] if role == 'server' else ['.'.join(map(str, n['prefix'] + [i])) for i in range(n['start'], n['end'] + 1)]
        for ip in ips:
            s = n if role == 'server' else servers.get(n['server'])
            out.append(dict(n, host=ip, role=role, endpoint=dict(host=s['host'], port=s['servicePort'], scheme=s['scheme']) if s else None))
    if selected is not None and not isinstance(selected, list): raise ValueError('本次目标必须为 IP 列表')
    if selected:
        wanted = {ipv4(ip) for ip in selected}
        unknown = wanted - {n['host'] for n in out}
        if unknown: raise ValueError('本次目标不在已保存清单中：' + ', '.join(sorted(unknown)))
        out = [n for n in out if n['host'] in wanted]
    return out


def preview(c, role, selected=None):
    return [dict(host=n['host'], os=n['os'], port=n['port'], user=n['user'], endpoint=n['endpoint']) for n in targets(c, role, selected)]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def cache_artifact(root, kind, spec):
    root.mkdir(parents=True, exist_ok=True)
    expected = spec['sha256']
    if expected:
        cached = root / (kind + '-' + expected)
        if cached.is_file() and digest(cached) == expected: return cached
    temp = root / (uuid.uuid4().hex + '.part')
    try:
        if spec['path']:
            source = Path(spec['path'])
            if not source.is_file(): raise ValueError(kind + '：本地文件不存在')
            shutil.copyfile(source, temp)
        elif spec['url']:
            if not expected: raise ValueError(kind + '：下载前请提供发布方 SHA256')
            try:
                req = urllib.request.Request(spec['url'], headers={'User-Agent': 'CSGOJ-Deployment-Console'})
                with urllib.request.urlopen(req, timeout=60) as r, temp.open('wb') as f:
                    if urlsplit(r.url).scheme != 'https': raise ValueError('下载重定向必须使用 HTTPS')
                    shutil.copyfileobj(r, f, 1024 * 1024)
            except urllib.error.HTTPError as e:
                if e.code not in (403, 404): raise ValueError(f'下载失败（HTTP {e.code}）') from None
                authenticated_download(spec['url'], temp)
        else: raise ValueError(kind + '：请提供本地产物路径或下载地址')
        if temp.stat().st_size == 0: raise ValueError(kind + '：产物为空')
        actual = digest(temp)
        if expected and expected != actual: raise ValueError(kind + '：SHA256 校验失败')
        with temp.open('rb') as f: magic = f.read(4)
        if (kind.endswith('linux') and magic != b'\x7fELF') or (kind.endswith('windows') and magic[:2] != b'MZ'):
            raise ValueError(kind + '：请提供单文件可执行产物，不能直接使用 ZIP 或下载页面')
        cached = root / (kind + '-' + actual)
        temp.replace(cached)
        return cached
    finally:
        temp.unlink(missing_ok=True)


def wsl_path(path):
    p = Path(path).resolve()
    if not re.fullmatch('[A-Za-z]:', p.drive): raise ValueError('缓存必须放在 Windows 本地盘符路径')
    return '/mnt/' + p.drive[0].lower() + p.as_posix()[2:]


def bundle(c, role, action, artifacts, selected=None):
    inventory = {'all': {'hosts': {}}}
    plays = []
    # Grouped plays allow forks to work across each platform; no credentials in task bodies.
    for platform in ('linux', 'windows'):
        names = []
        for n in (n for n in targets(c, role, selected) if n['os'] == platform):
            if action == 'deploy' and n['endpoint'] and n['endpoint']['scheme'] != 'https':
                raise ValueError('部署前请将管控业务地址配置为 HTTPS')
            alias = 'node_' + n['host'].replace('.', '_')
            names.append(alias)
            v = dict(ansible_host=n['host'], ansible_port=n['port'], ansible_user=n['user'], ansible_password=n['password'],
                     arena_config=json.dumps(dict(role=role, action=action, endpoint=n['endpoint']), ensure_ascii=False))
            if platform == 'linux':
                v.update(ansible_connection='paramiko', ansible_become=n['user'] != 'root', ansible_become_method=n['elevation'],
                         ansible_become_user='root', ansible_become_password=n['rootPassword'] or n['password'], ansible_python_interpreter='/usr/bin/python3')
            else:
                v.update(ansible_connection='winrm', ansible_winrm_transport='ntlm', ansible_winrm_scheme='https', ansible_winrm_server_cert_validation='validate')
            if n.get('privateKeyPath'):
                v.pop('ansible_password', None)
                if n['user'] == 'root': v.pop('ansible_become_password', None)
                v['ansible_paramiko_look_for_keys'] = False
                v.update(_dt_key_path=n['privateKeyPath'], _dt_key_passphrase=n.get('keyPassphrase', ''))
                if platform == 'windows':
                    for k in list(v):
                        if k.startswith('ansible_winrm_'): v.pop(k)
                    v.update(ansible_connection='ssh', ansible_shell_type='powershell', ansible_ssh_transfer_method='sftp',
                             ansible_ssh_extra_args='-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes')
            inventory['all']['hosts'][alias] = v
        if not names: continue
        linux = platform == 'linux'
        tasks = []
        base = '/opt/csgarena-deploy' if linux else r'C:\ProgramData\CSGArenaDeploy'
        if action in ('deploy', 'distribute'):
            path = artifacts[role + '-' + platform]
            tasks.append(dict(name='Create deployment directory', **{('ansible.builtin.file' if linux else 'ansible.windows.win_file'): dict(path=base, state='directory', **({'mode': '0700'} if linux else {}))}))
            if not linux:
                tasks.append(dict(name='Protect staging directory', **{'ansible.windows.win_powershell': dict(script=r"& icacls.exe 'C:\ProgramData\CSGArenaDeploy' /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'Cannot protect deployment ACL' }")}))
            # A digest-specific file avoids overwriting a running Windows executable.
            dest = base + ('/' if linux else '\\') + digest(path) + ('' if linux else '.exe')
            tasks.append(dict(name='Copy verified local artifact over LAN', **{('ansible.builtin.copy' if linux else 'ansible.windows.win_copy'): dict(src=wsl_path(path), dest=dest, **({'mode': '0700'} if linux else {}))}))
        else: dest = ''
        if action == 'distribute':
            tasks.append(dict(name='Verified artifact staged; existing service unchanged', **{'ansible.builtin.debug': dict(msg=dict(version=c['releaseTag'], path=dest, sha256=digest(path)))}))
            plays.append(dict(name=role + ' / ' + platform, hosts=':'.join(names), gather_facts=False, tasks=tasks))
            continue
        if linux:
            module = {'ansible.builtin.command': dict(argv=['python3', '-c', LINUX_SCRIPT, '{{ arena_config }}', dest])}
        else:
            module = {'ansible.windows.win_powershell': dict(script=WINDOWS_SCRIPT, parameters=dict(ConfigJson='{{ arena_config }}', Binary=dest))}
        tasks.append(dict(name='Install and enroll' if action == 'deploy' else 'Query service status', **module, register='arena_result', changed_when=False if action == 'status' else True))
        tasks.append(dict(name='Result', **{'ansible.builtin.debug': dict(msg="{{ arena_result.stdout | default(arena_result.output | default([])) }}")}))
        plays.append(dict(name=role + ' / ' + platform, hosts=':'.join(names), gather_facts=False, tasks=tasks))
    # SSH can authenticate yet hang opening a channel. Bound each task so a
    # single unresponsive host cannot indefinitely block the linear strategy.
    for play in plays:
        for task in play['tasks']:
            task['timeout'] = 300 if task['name'] in ('Copy verified local artifact over LAN', 'Install and enroll') else 60
    return dict(inventory=inventory, playbook=plays)


# Inventory only lives in a private WSL temporary directory, never on /mnt/c.
CONTROLLER = r'''
import json, os, pathlib, shutil, subprocess, sys, tempfile, contextlib
''' + WSL_RELAY + r'''
data=json.load(sys.stdin)
exe=os.path.expanduser('~/.local/share/csgoj-ansible/venv/bin/ansible-playbook')
if not os.path.isfile(exe): exe=shutil.which('ansible-playbook') or os.path.expanduser('~/.local/bin/ansible-playbook')
if not os.path.isfile(exe):
    sys.exit('WSL needs ansible-playbook; see docs/arena-deployment.md')
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='csgarena-ansible-') as d, contextlib.ExitStack() as cleanup:
    # Paramiko reads ~/.ssh/known_hosts. Give this job an isolated HOME containing
    # only the exact pins verified on Windows; never alter the user's WSL trust.
    home=pathlib.Path(d,'home'); ssh=home/'.ssh'; ssh.mkdir(parents=True)
    known=ssh/'known_hosts'; known.write_text(data.pop('known_hosts',''),encoding='ascii')
    keyfiles={}
    for ref,content in data.pop('keys',{}).items():
        path=pathlib.Path(d,'key-'+str(len(keyfiles)))
        path.write_text(content,encoding='ascii'); path.chmod(0o600)
        keyfiles[ref]=str(path)
    for host in data['inventory']['all']['hosts'].values():
        ref=host.pop('_dt_key_ref',None)
        if ref is not None: host['ansible_private_key_file']=keyfiles[ref]
        if host.get('ansible_connection') == 'ssh':
            host['ansible_ssh_extra_args'] = host.get('ansible_ssh_extra_args','') + ' -o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + str(known)
    proxy=setup_egress(data)
    if proxy: cleanup.callback(proxy.stop)
    for name,key in [('inventory.json','inventory'),('playbook.json','playbook')]:
        pathlib.Path(d,name).write_text(json.dumps(data[key]),encoding='utf-8')
    env=dict(os.environ, ANSIBLE_HOST_KEY_CHECKING='True', ANSIBLE_PARAMIKO_HOST_KEY_AUTO_ADD='False', ANSIBLE_NOCOLOR='1', ANSIBLE_RETRY_FILES_ENABLED='False')
    env.setdefault('ANSIBLE_COLLECTIONS_PATH',os.path.expanduser('~/.ansible/collections')+':/usr/share/ansible/collections')
    env['HOME']=str(home)
    env.pop('ANSIBLE_LOG_PATH',None)
    if keyfiles: env.pop('SSH_AUTH_SOCK',None)
    result=subprocess.run([exe,'-i',d+'/inventory.json',d+'/playbook.json','--forks','10'],cwd=d,env=env)
    sys.exit(result.returncode)
'''

CONTROLLER_ENV = r'''
import importlib.util, json, os, pathlib, shutil, subprocess, sys
install=sys.argv[1]=='prepare'
base=pathlib.Path.home()/'.local/share/csgoj-ansible'
venv=base/'venv'; exe=venv/'bin/ansible-playbook'
if install:
    base.mkdir(parents=True,exist_ok=True)
    if not (venv/'bin/python').exists():
        subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    subprocess.run([str(venv/'bin/python'),'-m','pip','install','ansible-core>=2.19,<2.20','paramiko>=3.5,<4','pywinrm>=0.5,<0.6'],check=True)
    subprocess.run([str(venv/'bin/ansible-galaxy'),'collection','install','ansible.windows:>=3.2.0,<4.0.0','--timeout','120'],check=True)
if not exe.exists(): exe=pathlib.Path(shutil.which('ansible-playbook') or os.path.expanduser('~/.local/bin/ansible-playbook'))
if not exe.is_file(): sys.exit('Missing Ansible. Use Prepare controller; WSL needs Python 3.11+ and python3-venv.')
subprocess.run([str(exe),'--version'],check=True)
python=exe.parent/'python'
if not python.exists(): python=pathlib.Path(sys.executable)
subprocess.run([str(python),'-c','import paramiko, winrm; print("SSH / WinRM Python dependencies OK")'],check=True)
subprocess.run([str(exe.parent/'ansible-doc'),'-t','module','ansible.windows.win_powershell'],stdout=subprocess.DEVNULL,check=True)
print('Controller ready. Target credentials, SSH trust and WinRM certificates are checked on deployment.')
'''


class ArenaDeployment:
    def __init__(self, store):
        self.root = store.root
        self.pending_trust = None
        self.path = store.root / 'arena.dpapi'
        self.cache = store.root / 'arena-cache'
        self.lock = threading.Lock()
        self.running = False
        self.state = dict(running=False, status='尚未执行', logs=[], targets=[])

    def config(self):
        if not self.path.exists(): return defaults()
        c = json.loads(dpapi(self.path.read_bytes(), True))
        if 'releaseTag' not in c:
            # Untagged installs may already be pinned to the original official URLs.
            sources = c.get('artifacts', {})
            pinned = any(sources.get(kind, {}).get('url') == asset_url(kind, 'v1.11.0') for kind in KINDS)
            c['releaseTag'] = 'v1.11.0' if pinned else DEFAULT_TAG
        c.setdefault('artifacts', {})
        if c['releaseTag'] == DEFAULT_TAG:
            for kind, spec in default_artifacts().items():
                if not any(c['artifacts'].get(kind, {}).values()): c['artifacts'][kind] = spec
        return c

    def save(self, raw):
        c = normalize(raw)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_bytes(dpapi(json.dumps(c, ensure_ascii=False).encode()))
        restrict(tmp)
        tmp.replace(self.path)
        return c

    def status(self):
        with self.lock: return copy.deepcopy(self.state)

    def log(self, line):
        # Connection errors from Ansible are never allowed to echo saved secrets.
        c = self.config()
        for n in c['servers'] + c['clients']:
            for k in ('password', 'rootPassword', 'keyPassphrase'):
                if n.get(k):
                    for secret in (n[k], json.dumps(n[k])[1:-1]): line = line.replace(secret, '[REDACTED]')
        with self.lock:
            self.state['logs'].append(line)
            self.state['logs'] = self.state['logs'][-800:]

    def start(self, role, action, selected=None):
        if action not in ('cache', 'distribute', 'deploy', 'status', 'check', 'prepare'): raise ValueError('操作无效')
        c = normalize(self.config())
        nodes = targets(c, role, selected)
        if action == 'deploy' and any(n['endpoint'] is None for n in nodes):
            raise ValueError('选手端清单已保存，请先为全部目标选择管控服务端，再部署登记')
        if not nodes and action not in ('cache', 'check', 'prepare'): raise ValueError('请先添加目标')
        with self.lock:
            if self.running: raise ValueError('管控任务正在运行')
            self.running = True
            self.pending_trust = None
            self.state = dict(running=True, status='执行中', logs=[], targets=preview(c, role, selected), role=role, action=action, id=uuid.uuid4().hex)
        threading.Thread(target=self._run, args=(c, role, action, selected), daemon=True).start()
        return dict(ok=True)

    def trust(self, job_id):
        with self.lock:
            pending = self.pending_trust
            if self.running or not pending or self.state.get('id') != job_id:
                raise ValueError('指纹确认已过期，请重新执行任务')
            c, role, action, records, selected = pending
            if normalize(self.config()) != c:
                raise ValueError('目标配置已变化，请重新执行任务并核对指纹')
            accept_hosts(self.root, records)
            self.pending_trust = None
        return self.start(role, action, selected) if selected else self.start(role, action)

    def _run(self, c, role, action, selected=None):
        try:
            paths = {}
            if action in ('cache', 'deploy', 'distribute'):
                needed = {role + '-' + os for os in ('linux', 'windows')} if action == 'cache' else {role + '-' + n['os'] for n in targets(c, role, selected)}
                self.log('本次配置版本：' + c['releaseTag'])
                for key in sorted(needed):
                    self.log('准备本地缓存：' + key)
                    paths[key] = cache_artifact(self.cache, key, c['artifacts'][key])
                    c['artifacts'][key]['sha256'] = digest(paths[key])
                    self.log('校验通过：' + paths[key].name)
                # All artifacts must be ready before contacting any host.
                self.save(c)
                with self.lock: self.state['cached'] = {key: c['artifacts'][key]['sha256'] for key in paths}
            if action != 'cache':
                self.log('WSL Ansible 开始；SSH 主机密钥和 WinRM HTTPS 证书必须已受信任。')
                environment = action in ('check', 'prepare')
                data = {} if environment else bundle(c, role, action, paths, selected)
                if not environment:
                    snapshot = network.snapshot()
                    records = inspect_hosts(self.root, targets(c, role, selected), snapshot)
                    unknown = [r for r in records if not r['trusted']]
                    if unknown:
                        with self.lock:
                            self.pending_trust = (c, role, action, unknown, selected)
                            self.state['trust'] = [{k: r[k] for k in ('host','algorithm','fingerprint')} for r in unknown]
                            self.state['status'] = '等待首次 SSH 指纹确认'
                        self.log('尚未登录或推送。请在下方核对首次连接的 SSH 主机指纹，然后确认继续。')
                        return
                    data['known_hosts'] = known_hosts_text(records)
                    self.log(f'SSH 主机指纹验证通过：{len(records)} 台；使用本任务独立信任文件。')
                    prepare_keys(data)
                    launcher = [wsl_path(sys.executable)]
                    if not getattr(sys, 'frozen', False): launcher.append(str(Path(__file__).with_name('server.py')))
                    launcher.append('--network-connect')
                    data['egress'] = {}
                    seen = set()
                    for n in targets(c, role, selected):
                        route = network.select(snapshot, n['host'])
                        detail = network.describe(route)
                        if detail not in seen: self.log('网络出口：' + detail); seen.add(detail)
                        data['egress'][n['host'] + ':' + str(n['port'])] = launcher + [n['host'], str(n['port']), route['source'] if route else '', str(route['interface']) if route else '0']
                command = ['wsl.exe', '-d', c['distro'], '--', 'python3', '-c', CONTROLLER_ENV if environment else CONTROLLER]
                if environment: command.append(action)
                p = subprocess.Popen(command,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', creationflags=0x08000000 if os.name == 'nt' else 0)
                try:
                    p.stdin.write(json.dumps(data)); p.stdin.close()
                    for line in p.stdout: self.log(line.rstrip())
                    if p.wait(): raise RuntimeError('Ansible 执行失败；按上方主机记录处理后重试')
                finally:
                    p.stdout.close()
                    if p.poll() is None: p.terminate(); p.wait()
            with self.lock: self.state['status'] = '完成' if action != 'status' else '状态查询完成（详见各机输出）'
        except Exception as exc:
            # URLs can contain signed credentials. Network exceptions are summarized.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__ + '：操作失败，请检查本地路径、下载连接及 WSL 环境'
            self.log(message)
            with self.lock: self.state['status'] = '失败'
        finally:
            with self.lock: self.running = False; self.state['running'] = False
