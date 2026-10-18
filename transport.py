import base64
import hashlib
import json
import os
from pathlib import Path
from ssh_alias import install_alias
import shlex
import time
import uuid
import paramiko

class PinPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, store, log): self.store, self.log = store, log
    def missing_host_key(self, client, hostname, key):
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        self.log('首次连接登记 SSH 主机指纹：' + hostname + ' ' + fingerprint)
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self.store.root / 'known_hosts'))

class Remote:
    def __init__(self, node, store, log): self.n, self.store, self.log = node, store, log; self.ssh = None
    def connect(self, bootstrap=False, password_only=False):
        if password_only and not self.n.get('password'):
            raise ValueError('请填写 SSH 登录密码；密码测试不会使用本地密钥')
        n = self.n; self.ssh = paramiko.SSHClient()
        system_known = Path.home() / '.ssh' / 'known_hosts'
        if system_known.exists(): self.ssh.load_system_host_keys(str(system_known))
        known = self.store.root / 'known_hosts'
        if known.exists(): self.ssh.load_host_keys(str(known))
        self.ssh.set_missing_host_key_policy(PinPolicy(self.store, self.log))
        keyfile = self.store.root / 'keys' / n['keyName']
        args = dict(hostname=n['host'], port=n['sshPort'], username=n['user'], timeout=12, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
        if not password_only and keyfile.exists(): args['pkey'] = paramiko.PKey.from_path(keyfile)
        if n.get('password'): args['password'] = n['password']
        try: self.ssh.connect(**args)
        except Exception:
            self.close(); raise
        self.ssh.get_transport().set_keepalive(20)
        if bootstrap:
            self.log(n['id'] + '：首次连接成功，正在写入服务器公钥')
            keyfile, pub = self.store.key(n['keyName'])
            self.run('umask 077; mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"; touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"; grep -qxF -- ' + shlex.quote(pub) + ' "$HOME/.ssh/authorized_keys" || printf "\\n%s\\n" ' + shlex.quote(pub) + ' >> "$HOME/.ssh/authorized_keys"')
            self.close()
            self.ssh = paramiko.SSHClient()
            if system_known.exists(): self.ssh.load_system_host_keys(str(system_known))
            if known.exists(): self.ssh.load_host_keys(str(known))
            self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            args.pop('password', None); args['pkey'] = paramiko.PKey.from_path(keyfile)
            try: self.ssh.connect(**args)
            except paramiko.AuthenticationException as e:
                self.close()
                raise paramiko.AuthenticationException('公钥已写入，但纯密钥复验被服务器拒绝；请检查 sshd 公钥认证设置和用户目录权限') from e
            self.ssh.get_transport().set_keepalive(20)
            self.log(n['id'] + '：公钥登录复验通过')
            try:
                command = install_alias(n, self.store, getattr(self, 'alias_dir', None))
            except Exception:
                self.close(); raise
            self.log(n['id'] + '：本机 SSH 别名已保存，可在 PowerShell 执行：' + command)
        return self
    def close(self):
        if self.ssh: self.ssh.close()
    def run(self, cmd, root=False, timeout=7200):
        mode = self.n.get('elevation', 'sudo')
        pw = self.n.get('elevatePassword') or self.n.get('password', '')
        if root and self.n['user'] != 'root':
            if mode == 'su': cmd = 'stty -echo; printf "CSGREADY\\n"; su - root -c ' + shlex.quote(cmd)
            else: cmd = 'sudo -S -p "" -- bash -c ' + shlex.quote(cmd)
        chan = self.ssh.get_transport().open_session()
        if root and mode == 'su' and self.n['user'] != 'root': chan.get_pty()
        chan.exec_command(cmd)
        pending_password = root and self.n['user'] != 'root'
        if pending_password and mode != 'su':
            chan.sendall(pw + '\n'); chan.shutdown_write(); pending_password = False
        deadline = time.monotonic() + timeout; buf = ''; output = []
        while True:
            chunks = []
            if chan.recv_ready(): chunks.append(chan.recv(65536))
            if chan.recv_stderr_ready(): chunks.append(chan.recv_stderr(65536))
            for chunk in chunks:
                s = chunk.decode('utf-8', errors='replace'); output.append(s); buf += s
                if pending_password and 'CSGREADY' in buf:
                    chan.sendall(pw + '\n'); pending_password = False
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    if not line.startswith('CSGRESULT:'): self.log(line.rstrip())
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready(): break
            if time.monotonic() > deadline:
                chan.close(); raise TimeoutError('远程命令超时；可能仍在执行，请检查远端后再重试')
            time.sleep(.05)
        code = chan.recv_exit_status(); chan.close()
        if buf and not buf.startswith('CSGRESULT:'): self.log(buf)
        if code: raise RuntimeError(self.n['id'] + '：远程步骤失败，退出码 ' + str(code))
        return ''.join(output)
    def put(self, local, remote):
        with self.ssh.open_sftp() as s: s.put(str(local), remote)
    def get(self, remote, local):
        with self.ssh.open_sftp() as s: s.get(remote, str(local))
    def op(self, action, config, tool=None, extra=None, archive=None):
        # Temporary files are readable only by the SSH account/root, and removed afterwards.
        tmp = None
        try:
            with self.ssh.open_sftp() as s:
                home = s.normalize('.')
                tmp = home + '/.csgoj-job-' + uuid.uuid4().hex
                s.mkdir(tmp, 0o700)
                base = Path(__file__).resolve().parent
                s.put(str(base / 'remote_ops.py'), tmp + '/remote_ops.py')
                s.put(str(base / 'database_storage.py'), tmp + '/database_storage.py')
                if tool: s.put(str(tool), tmp + '/deployer')
                if archive:
                    last = [-1]
                    def progress(done, total):
                        pct = int(done * 100 / max(total, 1))
                        if pct // 5 != last[0]:
                            last[0] = pct // 5
                            self.log(f'{self.n["id"]}：镜像上传 {pct}%（{done/1048576:.0f}/{total/1048576:.0f} MB）')
                    s.put(str(archive), tmp + '/image.tar', callback=progress)
                remote_node = {k: v for k, v in self.n.items() if k not in ('password', 'elevatePassword')}
                remote_config = {k: config[k] for k in ('version', 'mirror', 'disableGui', 'neverSleep', 'performance', 'tool') if k in config}
                payload = dict(action=action, node=remote_node, config=remote_config, extra=extra or {}, tool=tmp + '/deployer' if tool else '')
                if archive: payload['extra'] = dict(payload['extra'], archive=tmp + '/image.tar')
                with s.open(tmp + '/job.json', 'w') as f: f.write(json.dumps(payload))
                s.chmod(tmp + '/job.json', 0o600)
            text = self.run('python3 -B ' + shlex.quote(tmp + '/remote_ops.py') + ' ' + shlex.quote(tmp + '/job.json'), root=action!='adoptInspect')
            results = [line[len('CSGRESULT:'):] for line in text.splitlines() if line.startswith('CSGRESULT:')]
            return json.loads(results[-1]) if results else {}
        finally:
            try:
                if tmp is None: raise IOError('临时目录尚未创建')
                with self.ssh.open_sftp() as s:
                    for f in ('job.json', 'remote_ops.py', 'database_storage.py', 'deployer', 'image.tar'):
                        try: s.remove(tmp + '/' + f)
                        except IOError: pass
                    try: s.rmdir(tmp)
                    except IOError: pass
            except Exception:
                if tmp: self.log('SSH 已中断，远端临时文件可能仍在 '+tmp+'；原错误保留，请检查远端步骤后再重试')
