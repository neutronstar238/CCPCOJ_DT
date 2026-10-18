"""WSL deployment using the same offline archives and journaled operations as SSH."""
import copy
import datetime
import ipaddress
import json
import secrets
from pathlib import Path
import subprocess
import threading
import uuid
from urllib.parse import urlsplit

from artifacts import ImageCache, file_hash
from engine import Engine
from models import normalize, protect_managed
from storage import dpapi, restrict


def wsl_list():
    try:
        p = subprocess.run(['wsl.exe', '--list', '--quiet'], capture_output=True, timeout=20,
                           creationflags=0x08000000)
    except FileNotFoundError:
        return []
    if p.returncode: raise RuntimeError('WSL 不可用，请先安装 WSL 2 和 Ubuntu 24.04')
    text = p.stdout.decode('utf-16-le' if b'\x00' in p.stdout else 'utf-8', errors='replace')
    return [v.strip() for v in text.splitlines() if v.strip() and not v.strip().startswith('docker-desktop')]


class LocalStore:
    # Share only caches/log directories; never overwrite remote-node config.dpapi.
    def __init__(self, parent): self.parent, self.root = parent, parent.root
    def load(self):
        p = self.root / 'local-deploy.dpapi'
        return json.loads(dpapi(p.read_bytes(), True)) if p.exists() else None
    def save(self, config):
        config=copy.deepcopy(config)
        for key in ('localStart','localInspect','localStop'): config.pop(key,None)
        p = self.root / 'local-deploy.dpapi'
        tmp = p.with_suffix('.tmp')
        tmp.write_bytes(dpapi(json.dumps(config, ensure_ascii=False).encode()))
        restrict(tmp); tmp.replace(p)


class WSLRemote:
    def __init__(self, node, store, log): self.n, self.store, self.log = node, store, log
    def connect(self, bootstrap=False): return self
    def close(self): pass
    def command(self, args, input=None, timeout=7200, quiet=False):
        # Ignore Windows/Docker Desktop contexts and proxies; use this distro's own daemon.
        cmd = ['wsl.exe', '-d', self.n['distro'], '-u', 'root', '--exec',
               'env', '-u', 'DOCKER_CONTEXT', 'DOCKER_HOST=unix:///var/run/docker.sock',
               'PYTHONUNBUFFERED=1'] + args
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=self.store.root, creationflags=0x08000000)
        expired = threading.Event()
        def timeout_job(): expired.set(); p.kill()
        timer = threading.Timer(timeout, timeout_job); timer.start()
        lines = []
        try:
            if input is not None: p.stdin.write(input.encode('utf-8'))
            p.stdin.close()
            for raw in p.stdout:
                line = raw.decode('utf-8', errors='replace').replace('\x00', '').rstrip()
                lines.append(line)
                if not quiet and not line.startswith('CSGRESULT:'): self.log(line)
            p.wait()
            if expired.is_set(): raise RuntimeError('WSL 操作超时；请先检测本地状态再重试')
            if p.returncode: raise RuntimeError('WSL 操作失败：'+ ('\n'.join(lines)[-2000:] or str(p.returncode)))
            return '\n'.join(lines)
        finally: timer.cancel(); p.stdout.close()
    def run(self, cmd, root=False, timeout=7200):
        return self.command(['bash', '-c', cmd], timeout=timeout)
    def windows_path(self, path):
        return self.command(['wslpath', '-a', '-u', str(Path(path).resolve())], quiet=True, timeout=20).strip()
    def put(self, local, remote):
        self.command(['cp', '--', self.windows_path(local), remote], quiet=True)
    def get(self, remote, local):
        self.command(['cp', '--', remote, self.windows_path(local)], quiet=True)
    def op(self, action, config, tool=None, extra=None, archive=None):
        tmp = '/tmp/csgoj-local-' + uuid.uuid4().hex
        self.command(['mkdir', '-m', '700', tmp], quiet=True)
        try:
            for name in ('remote_ops.py', 'database_storage.py'):
                self.put(Path(__file__).parent / name, tmp + '/' + name)
            if tool: self.put(tool, tmp + '/deployer')
            payload = dict(action=action, node=self.n, config=config, extra=extra or {},
                           tool=tmp+'/deployer' if tool else '')
            if archive:
                self.log('复制 Windows 缓存到 WSL：'+Path(archive).name)
                self.put(archive, tmp+'/image.tar')
                payload['extra'] = dict(payload['extra'], archive=tmp+'/image.tar')
            self.command(['python3', '-c', 'import sys,os;os.umask(0o077);open(sys.argv[1],"w").write(sys.stdin.read())', tmp+'/job.json'],
                         input=json.dumps(payload), quiet=True)
            output = self.command(['python3', '-B', tmp+'/remote_ops.py', tmp+'/job.json'])
            return json.loads(next(v[10:] for v in reversed(output.splitlines()) if v.startswith('CSGRESULT:')))
        finally:
            # Only delete this exact randomly-created temporary job directory.
            try: self.command(['rm', '-rf', '--', tmp], quiet=True, timeout=30)
            except Exception: self.log('临时目录待清理：'+tmp)


class LocalEngine(Engine):
    def keep_alive(self, distro):
        """WSL systemd services alone do not prevent the distro's idle shutdown."""
        if not hasattr(self,'keepers'): self.keepers={}
        p=self.keepers.get(distro)
        if p is None or p.poll() is not None:
            self.keepers[distro]=subprocess.Popen(
                ['wsl.exe','-d',distro,'-u','root','--exec','flock','-n',
                 '/run/lock/csgoj-local-keepalive.lock','sleep','infinity'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                cwd=self.store.root, creationflags=0x08000000)
    def _job(self, action, selected, source):
        try:
            n = self.node(selected[0]); r = self.remote(n)
            if action in ('batchUpdate','startService') or self.c.get('localStart'):
                self.keep_alive(n['distro'])
            r.command(['sh', '-c', 'test "$(uname -m)" = x86_64 && grep -q \'VERSION_ID="24.04"\' /etc/os-release'], quiet=True, timeout=30)
            # Docker Desktop binds Linux paths in a different VM, so it cannot host these volumes.
            check = r.command(['sh', '-c', 'if command -v docker >/dev/null && docker info >/dev/null 2>&1; then docker info --format "{{.OperatingSystem}}"; fi'], quiet=True, timeout=30)
            if 'docker desktop' in check.lower():
                raise ValueError('请关闭此发行版的 Docker Desktop WSL 集成，改用发行版内 Docker；避免数据目录挂载到另一台虚拟机')
            ips = r.command(['hostname', '-I'], quiet=True, timeout=20).split()
            address = next((v for v in ips if ipaddress.ip_address(v).version == 4), None)
            if not address: raise ValueError('WSL 未取得 IPv4 地址')
            for node in self.c['nodes']: node['host'] = address
        except Exception as exc:
            for nid in selected:
                self.state['nodes'][nid] = dict(serviceStatus='未知（WSL 检测未完成）',
                    checkedAt=datetime.datetime.now().isoformat(timespec='seconds'), error='WSL 检测未通过，请查看日志')
            self.log('失败：'+str(exc)); self.state.update(status='失败 / 需要检查', running=False); self.running=False
            return
        super()._job(action, selected, source)

    def start_local(self, action, config, selected):
        if action in ('syncVersions','health'): config = dict(config, localInspect=True)
        if action == 'stop': return self.start('health', dict(config, localStop=True), selected)
        if action != 'start': return self.start(action, config, selected)
        # Engine validates selection and serializes the job; route through its existing start action.
        if len(selected) == 1: return self.start('startService', config, selected)
        # Multiple starts are intentionally ordered Web before judge.
        return self.start('health', dict(config, localStart=True), selected)

    def stop_services(self, selected):
        nodes = sorted((self.node(nid) for nid in selected), key=lambda n: n['kind'] == 'web')
        for n in nodes:
            if not n.get('prepared') or not n.get('deployed') or n['role'] != 'active':
                raise ValueError(n['id']+'：只能关闭本工具已部署的本地服务')
        for n in nodes:
            self.checkpoint()
            self.log(n['id']+'：关闭已有服务，保留容器、数据和配置')
            try: self.op(n, 'stop')
            finally: self.health([n['id']], allow_stopped=True)
            state = self.state['nodes'][n['id']]
            if any(c['running'] for c in state.get('containers', [])):
                raise RuntimeError(n['id']+'：仍有容器运行，请检查日志后重试')
            self.log(n['id']+'：已确认关闭，可使用一键开启恢复')

    def health(self, selected, allow_stopped=False):
        if self.c.pop('localStop', False): return self.stop_services(selected)
        allow_stopped = self.c.pop('localInspect', False) or allow_stopped
        if self.c.pop('localStart', False):
            for nid in sorted(selected, key=lambda v: self.node(v)['kind'] != 'web'):
                self.checkpoint(); self.start_service(self.node(nid))
            return
        inventories={nid:self.state['nodes'].get(nid,{}).get('inventory') for nid in selected}
        try: super().health(selected, allow_stopped=allow_stopped)
        finally:
            for nid,inv in inventories.items():
                if inv: self.state['nodes'].setdefault(nid,{})['inventory']=inv
        for nid in selected:
            state = self.state['nodes'].get(nid, {})
            if state.get('online'): state['serverStatus'] = '在线（本机 WSL）'

    def sync_versions(self, selected):
        super().sync_versions(selected)
        if self.c.pop('localInspect',False): self.health(selected, allow_stopped=True)

    def external(self, n): return n['kind']=='judge' and self.c['local'].get('judgeMode')=='external'
    def associated(self, web): return [n for n in super().associated(web) if not self.external(n)]
    def activate(self, n, rebuild=False):
        if n['kind']=='web': return super().activate(n, rebuild)
        client=previous=None
        if self.external(n): n['webUrl'] = self.c['local']['webUrl']
        else:
            web=self.node(n['web'])
            if not web.get('deployed'): raise ValueError('请先部署本地 Web，或同时勾选 Web 和评测机')
            gateway=self.remote(n).command(['docker','network','inspect','bridge','--format','{{(index .IPAM.Config 0).Gateway}}'],quiet=True).strip()
            ipaddress.ip_address(gateway)
            n['webUrl']='http://'+gateway+':'+str(web['port'])
            client,previous=self.judge_account(n,web)
        if rebuild: self.op(n, 'removeJudge')
        result = self.op(n, 'deploy')
        if client: result['oj']=client.heartbeat(n,previous)
        else: self.verify_service(n, result)
        n['deployed'] = True; n['deployedVersion'] = self.c['version']; self.save()
        self.state['nodes'][n['id']] = result
    def verify_service(self, n, result):
        if not self.external(n): return super().verify_service(n, result)
        actual = self.op(n, 'health')
        if len(actual['containers']) != n['pods'] or not all(c['running'] for c in actual['containers']):
            raise RuntimeError('本地评测容器未全部运行')
        result.update(actual, serviceStatus='容器已运行；外部 Web 心跳请在对应站点核对')
    def upgrade(self, n, prefer_remote=False):
        if not self.external(n): return super().upgrade(n, prefer_remote)
        target = self.check_update(n, prefer_remote=True)
        if not target['updateAvailable']: return
        begun = False
        try:
            self.op(n, 'stop'); begun=True
            self.op(n, 'updateBegin', dict(targetId=target['targetId']))
            self.op(n, 'start'); self.verify_service(n, {})
            self.op(n, 'updateCommit')
            n['preparedVersion']=n['deployedVersion']=self.c['version']; self.save()
        except Exception:
            if begun:
                self.op(n, 'updateRollback'); self.op(n, 'start')
            raise
    def snapshot(self, web):
        meta = self.op(web, 'snapshot', dict(user='root'))
        folder = self.store.root / 'backups' / ('local-'+uuid.uuid4().hex)
        folder.mkdir(); restrict(folder); dest=folder/'data.tar.gz'; r=self.remote(web)
        r.get(meta['path'], dest)
        if file_hash(dest) != meta['sha256']: raise RuntimeError('本地备份校验失败')
        restrict(dest)
        record=dict(path=str(dest),sha256=meta['sha256'],bytes=meta['bytes'],web=web['id'],version=web.get('deployedVersion'))
        self.c.setdefault('backups',[]).append(record); self.save()
        self.log('本地更新备份：'+str(dest)); return record


class LocalDeployment:
    def __init__(self, parent):
        self.parent=parent; self.store=LocalStore(parent)
        self.engine=LocalEngine(self.store, remote_factory=WSLRemote)
    @property
    def running(self): return self.engine.running
    def config(self):
        c=self.store.load()
        if c: return c
        common=dict(role='active',host='127.0.0.1',user='root',sshPort=22,elevation='root',localWSL=True,distro='')
        return normalize(dict(version='',toolVersion='',mirror='',disableGui=False,neverSleep=False,performance=False,
            local=dict(distro='',path='/srv/csgoj-local',judgeMode='local',webUrl=''),nodes=[
            dict(common,id='localweb',kind='web',keyName='localweb',site='csglocal',path='/srv/csgoj-local/web',port=28080,myadminPort=28050,adminUser='admin'),
            dict(common,id='localpcj',kind='judge',keyName='localpcj',path='/srv/csgoj-local/judge',web='localweb',pods=1,judgeUser='lj1')]))
    def describe(self):
        c=self.config(); cache=ImageCache(self.parent).describe()
        return dict(config=c,state=self.engine.status(),cache=cache,tool=(self.parent.load() or {}).get('tool'),
                    configPath=str(self.store.root/'local-deploy.dpapi'))
    def save_settings(self, settings):
        c=self.config(); old=copy.deepcopy(c); opt=c['local']
        for k in ('distro','path','judgeMode','webUrl'):
            if k in settings: opt[k]=str(settings[k]).strip()
        if opt['distro'] and (len(opt['distro'])>128 or any(ch in opt['distro'] for ch in '\x00\r\n')): raise ValueError('WSL 发行版名称不合法')
        if opt['judgeMode'] not in ('local','external'): raise ValueError('评测机绑定方式错误')
        if opt['webUrl']:
            u=urlsplit(opt['webUrl'])
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('外部 Web 地址须为 http/https 地址，不含账号密码')
        for n in c['nodes']:
            n.update(distro=opt['distro'],path=opt['path'].rstrip('/')+('/web' if n['kind']=='web' else '/judge'))
            fields=('port','myadminPort','adminUser') if n['kind']=='web' else ('pods','judgeUser','judgePassword')
            for f in fields:
                if f in settings: n[f]=settings[f]
            if n['kind']+'Version' in settings: n['targetVersion']=settings[n['kind']+'Version'].strip()
            if n['kind']=='web' and settings.get('adminPassword'): n['secrets']['admin']=settings['adminPassword']
            if n['kind']=='judge' and opt['judgeMode']=='local' and not n.get('judgePassword'):
                n['judgePassword']=secrets.token_hex(12)
        if any(n.get('prepared') for n in old['nodes']):
            if any(old['local'][k]!=opt[k] for k in ('distro','path')): raise ValueError('已有本地部署，不能直接改变发行版或目录；请保留原实例配置')
        if old['nodes'][1].get('prepared') and any(old['local'][k]!=opt[k] for k in ('judgeMode','webUrl')):
            raise ValueError('已有本地评测机，不能直接改变 Web 绑定')
        c=protect_managed(old,normalize(c)); self.store.save(c); return c
    def start(self, action, selected, confirmed=False):
        if action not in ('syncVersions','health','start','stop','batchUpdate','recoverUpdate'): raise ValueError('未知本地操作')
        c=self.config()
        if action in ('syncVersions','health') and not selected: selected=['localweb','localpcj']
        if c['local']['distro'] not in wsl_list(): raise ValueError('请先选择已安装的 Ubuntu 24.04 WSL 2 发行版')
        if not selected or not set(selected)<= {'localweb','localpcj'}: raise ValueError('请选择本地 Web 或评测机')
        if action in ('batchUpdate','recoverUpdate') and confirmed is not True: raise ValueError('请确认本地部署 / 更新操作')
        parent=self.parent.load() or {}
        c['tool']=parent.get('tool'); c['toolVersion']=parent.get('toolVersion','')
        if action=='batchUpdate' and 'localpcj' in selected and c['local']['judgeMode']=='external':
            judge=c['nodes'][1]
            if not c['local']['webUrl'] or not judge['judgePassword']: raise ValueError('请填写外部 Web 地址与已有评测账号口令')
            judge['webUrl']=c['local']['webUrl']
        return self.engine.start_local(action,c,selected)
