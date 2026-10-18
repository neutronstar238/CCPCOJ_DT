"""Synchronize local OpenSSH aliases without changing private keys or remote hosts."""
import datetime
import os
from pathlib import Path
import re
import tempfile
import threading

import paramiko

_lock = threading.Lock()


def _render_alias(node, store, directory, text, update_existing=False):
    path = directory / 'config'
    name = node['keyName']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', name):
        raise ValueError('SSH 别名格式错误')
    host, user, port = node['host'], node['user'], int(node['sshPort'])
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user) or not 1 <= port <= 65535:
        raise ValueError('SSH 别名连接参数错误')
    key = (store.root / 'keys' / name).resolve()
    if not key.is_file():
        raise ValueError('SSH 别名对应的私钥不存在')

    def quote(path):
        value = Path(path).resolve().as_posix()
        if any(c in value for c in '\r\n"'):
            raise ValueError('SSH 文件路径包含不支持的字符')
        return '"' + value.replace('%', '%%') + '"'

    begin, end = '# BEGIN CSGOJ SSH ' + name, '# END CSGOJ SSH ' + name
    pattern = re.compile(r'^' + re.escape(begin) + r'\r?\n.*?^' + re.escape(end) + r'\r?\n?', re.M | re.S)
    owned = pattern.findall(text)
    if len(owned) > 1 or (begin in text and not owned):
        raise ValueError('SSH 配置中的工具标记不完整，请检查 ' + str(path))
    remainder = pattern.sub('', text)
    cfg = paramiko.SSHConfig.from_text(remainder)
    if not owned and any(name.lower() == alias.lower() for alias in cfg.get_hostnames()):
        existing = cfg.lookup(name)
        identities = existing.get('identityfile', [])
        fingerprint = paramiko.PKey.from_path(key).fingerprint
        def same_identity(value):
            candidate = Path(value).expanduser().resolve()
            if candidate == key: return True
            try: return paramiko.PKey.from_path(candidate).fingerprint == fingerprint
            except (OSError, ValueError, paramiko.SSHException): return False
        same_key = any(same_identity(p) for p in identities)
        if not (same_key and (update_existing or (existing.get('hostname') == host and existing.get('user') == user and int(existing.get('port', 22)) == port))):
            raise ValueError('本机已有不同的 SSH 别名 ' + name + '；原配置未覆盖，请修改私钥名称或核对 ' + str(path))
    # Precede Host * defaults; reset the scope so original global options stay global.
    block = '\n'.join([begin, 'Host ' + name, '    HostName ' + host,
        '    User ' + user, '    Port ' + str(port), '    IdentityFile ' + quote(key),
        '    IdentitiesOnly yes', '    UserKnownHostsFile ' + quote(directory / 'known_hosts') + ' ' + quote(store.root / 'known_hosts'),
        '    ServerAliveInterval 30', 'Host *', end, ''])
    return block + remainder


def sync_aliases(nodes, store, ssh_dir=None, update_existing=False):
    directory = Path(ssh_dir) if ssh_dir else Path.home() / '.ssh'
    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'config'
        original = path.read_bytes() if path.exists() else b''
        text = original.decode('utf-8-sig')
        # Render every alias first; one conflict must not partially modify config.
        for node in nodes:
            text = _render_alias(node, store, directory, text, update_existing)
        updated = text.encode('utf-8')
        if updated != original:
            if path.exists():
                backup = directory / ('config.before-csgoj-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
                backup.write_bytes(original)
            fd, temp = tempfile.mkstemp(prefix='.csgoj-ssh-', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(updated)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
    return ['ssh ' + n['keyName'] for n in nodes]


def install_alias(node, store, ssh_dir=None):
    return sync_aliases([node], store, ssh_dir)[0]
