"""Independent SSH enrollment; no Docker, sudo or business deployment required."""
import copy
import datetime
import json
from pathlib import Path
import re
import threading
import uuid
import paramiko
from storage import dpapi, restrict
from models import normalize, protect_managed, redact
from transport import Remote
from ssh_alias import sync_aliases

FIELDS = ('keyName', 'host', 'user', 'sshPort', 'password')

def validate(profile):
    p = {k: profile.get(k, '') for k in FIELDS}
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', p['keyName']): raise ValueError('私钥文件名格式错误')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', p['host']): raise ValueError('请填写服务器 IPv4 或域名')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', p['user']): raise ValueError('SSH 登录账号格式错误')
    p['sshPort'] = int(p['sshPort'] or 22)
    if not 1 <= p['sshPort'] <= 65535: raise ValueError('SSH 端口错误')
    if not isinstance(p['password'], str): raise ValueError('SSH 密码格式错误')
    return p

class SSHManager:
    def __init__(self, store, remote_factory=Remote, ssh_dir=None):
        self.store, self.remote_factory = store, remote_factory
        self.ssh_dir = Path(ssh_dir) if ssh_dir else Path.home() / '.ssh'
        self.lock = threading.Lock(); self.running = False
        self.state = dict(running=False, logs=[], results={}, status='尚未执行 SSH 操作')
    def profiles(self):
        p = self.store.root / 'ssh-connections.dpapi'
        records = json.loads(dpapi(p.read_bytes(), True)) if p.exists() else []
        by_key = {r['keyName']: r for r in records}
        # The main node editor remains usable; saved node connections take precedence.
        for n in (self.store.load() or {}).get('nodes', []): by_key[n['keyName']] = {k: n.get(k, '') for k in FIELDS}
        return list(by_key.values())
    def describe(self):
        result = []
        for p in self.profiles():
            item = dict(p); path = self.store.root / 'keys' / p['keyName']
            item.update(localPath=str(path), exists=path.exists())
            if path.exists():
                try: item['fingerprint'] = paramiko.PKey.from_path(path).fingerprint
                except Exception: item['keyError'] = '私钥无法读取（可能已加密或格式不支持）'
            result.append(item)
        return result
    def save(self, records):
        if len(records) > 300: raise ValueError('最多 300 条 SSH 连接')
        profiles = [validate(r) for r in records]
        if len({p['keyName'] for p in profiles}) != len(profiles): raise ValueError('私钥名称重复')
        before = self.store.load(); updated = copy.deepcopy(before)
        if updated:
            mapping = {p['keyName']: p for p in profiles}
            for n in updated.get('nodes', []):
                if n['keyName'] in mapping: n.update(mapping[n['keyName']])
            if updated.get('nodes'): updated = protect_managed(before, normalize(updated))
        keyed = [p for p in profiles if (self.store.root / 'keys' / p['keyName']).is_file()]
        sync_aliases(keyed, self.store, self.ssh_dir, update_existing=True)
        path = self.store.root / 'ssh-connections.dpapi'; tmp = path.with_suffix('.tmp')
        tmp.write_bytes(dpapi(json.dumps(profiles, ensure_ascii=False).encode())); restrict(tmp)
        if updated: self.store.save(updated)
        tmp.replace(path)
        with self.lock:
            self.state['results'] = {p['keyName']: dict(host=p['host'], user=p['user'], sshPort=p['sshPort'], status='本机别名已同步：ssh ' + p['keyName'] + '；服务器登录未验证') for p in keyed}
        return self.describe()
    def inventory(self):
        path = self.ssh_dir / 'config'
        cfg = paramiko.SSHConfig.from_path(str(path)) if path.exists() else paramiko.SSHConfig()
        rows = {}; prefix = lambda name: bool(re.fullmatch(r'(?:web|pcj)[A-Za-z0-9_-]*', name, re.I)) and len(name)<=40
        for alias in sorted(cfg.get_hostnames()):
            if '*' in alias or '?' in alias or '!' in alias: continue
            c = cfg.lookup(alias); identities = c.get('identityfile', [])
            key = Path(identities[0]).expanduser() if identities else self.ssh_dir / alias
            if not key.is_absolute(): key = self.ssh_dir / key
            if not (prefix(alias) or prefix(key.name)): continue
            name = alias if prefix(alias) else key.name
            rows[name] = dict(keyName=name, host=c.get('hostname',alias), user=c.get('user','ccpc00'), sshPort=int(c.get('port',22)), password='', sourcePath=str(key))
        # Keys can exist before a matching SSH config entry; the user supplies the address.
        if self.ssh_dir.exists():
            for key in sorted(self.ssh_dir.iterdir()):
                if key.is_file() and prefix(key.name) and key.name not in rows:
                    rows[key.name] = dict(keyName=key.name,host='',user='ccpc00',sshPort=22,password='',sourcePath=str(key))
        for row in rows.values():
            key = Path(row['sourcePath'])
            row['exists'] = key.is_file()
            if row['exists']:
                try: row['fingerprint'] = paramiko.PKey.from_path(key).fingerprint
                except Exception: row['keyError'] = '私钥无法读取（可能已加密或格式不支持）'
        return list(rows.values())
    def import_existing(self, names, connections=None):
        if not names: raise ValueError('请先选择要导入的密钥')
        inventory = {p['keyName']: p for p in self.inventory()}
        overrides = {p['keyName']:p for p in (connections or [])}
        records = {p['keyName']: p for p in self.profiles()}; pending = []
        for name in names:
            if name not in inventory: raise ValueError('扫描结果中没有此密钥：' + str(name))
            p = inventory[name]
            if not p['exists'] or p.get('keyError'): raise ValueError('私钥不可用：' + name)
            target = self.store.root / 'keys' / name
            source = Path(p['sourcePath'])
            if target.exists() and paramiko.PKey.from_path(target).fingerprint != p['fingerprint']:
                raise ValueError('工具中已有不同的同名私钥，拒绝覆盖：' + name)
            candidate = dict(p)
            if name in overrides:
                candidate.update({k:overrides[name][k] for k in ('host','user','sshPort') if k in overrides[name]})
            if name not in records: records[name] = validate(candidate)
            pending.append((source, target, name))
        # Preflight completes before any copies; original keys are never moved/deleted.
        for source, target, name in pending:
            if not target.exists():
                with target.open('xb') as f: f.write(source.read_bytes())
                restrict(target)
            self.store.key(name)
        return self.save(list(records.values()))
    def log(self, msg):
        line = datetime.datetime.now().strftime('%H:%M:%S') + '  ' + redact(str(msg), {'nodes':self.profiles()})
        with self.lock: self.state['logs'] = (self.state['logs'] + [line])[-1000:]
    def status(self):
        with self.lock: return copy.deepcopy(self.state)
    def start(self, action, names):
        if action not in ('generate','authorize','test','testPassword'): raise ValueError('未知 SSH 操作')
        profiles = {p['keyName']: p for p in self.profiles()}
        if not names or not set(names) <= profiles.keys(): raise ValueError('请选择已保存的 SSH 连接')
        selected = [copy.deepcopy(profiles[name]) for name in dict.fromkeys(names)]
        with self.lock:
            if self.running: raise ValueError('SSH 操作正在执行')
            self.running = True; self.state.update(running=True, logs=[], status='执行中', job=uuid.uuid4().hex)
        threading.Thread(target=self._work,args=(action,selected),daemon=False).start()
    def _work(self, action, selected):
        failures = 0
        for p in selected:
            name = p['keyName']; r = None
            try:
                label = {'generate':'生成本地密钥','authorize':'自动配置免密 SSH','test':'仅测试密钥登录（不使用密码）','testPassword':'仅测试密码登录（不使用密钥）'}[action]
                self.log(name + '：' + label)
                if action in ('generate','authorize'): self.store.key(name)
                path = self.store.root / 'keys' / name
                if action != 'testPassword' and not path.exists(): raise ValueError('本地私钥尚未生成或导入；首次连接请点击“自动配置免密 SSH”')
                if action != 'generate':
                    n = dict(p, id=name)
                    if action == 'test': n['password'] = ''
                    r = self.remote_factory(n,self.store,self.log)
                    r.alias_dir = self.ssh_dir
                    if action == 'testPassword': r.connect(password_only=True)
                    else: r.connect(bootstrap=action=='authorize')
                message = {'generate':'本地密钥已就绪（未配置服务器）','authorize':'公钥已配置，纯密钥复验通过；PowerShell：ssh ' + name,'test':'纯密钥登录通过','testPassword':'密码登录通过（本次不写入公钥）'}[action]
                result = dict(ok=True, status=message, host=p['host'], user=p['user'], sshPort=p['sshPort'], checkedAt=datetime.datetime.now().isoformat(timespec='seconds'))
                self.log(name + '：' + message)
            except Exception as e:
                failures += 1
                message = ('服务器 SSH 主机指纹与已保存记录不一致，已停止连接；请核对 IP 与服务器身份后再更新 known_hosts。原记录保留。' if isinstance(e, paramiko.BadHostKeyException) else str(e))
                if isinstance(e, paramiko.AuthenticationException):
                    if action == 'test': message = '密钥登录被拒绝；本次未使用密码。首次连接请点击“自动配置免密 SSH”写入公钥。'
                    elif action == 'testPassword': message = '密码登录被服务器拒绝；本次未使用密钥。请核对登录账号、SSH 密码及服务器是否允许密码认证。'
                    elif '公钥已写入' not in message: message = '配置公钥前的 SSH 登录被拒绝，尚未写入公钥；请先点击“测试密码登录”检查账号和密码。'
                error = redact(message, {'nodes':selected})
                result = dict(ok=False, status=error, host=p['host'], user=p['user'], sshPort=p['sshPort'])
                self.log(name + '：失败：' + error)
            finally:
                if r: r.close()
            with self.lock: self.state['results'][name] = result
        with self.lock:
            self.running = False; self.state.update(running=False, status='完成' if not failures else f'{failures} 条连接未完成，请检查日志')
