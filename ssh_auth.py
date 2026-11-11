"""External private key authentication shared by all SSH entry points."""
from pathlib import Path
import json
import paramiko
from cryptography.hazmat.primitives import serialization

FIELDS = ('privateKeyPath', 'keyPassphrase')


def normalize_auth(record):
    path = str(record.get('privateKeyPath', '')).strip()
    phrase = record.get('keyPassphrase', '')
    if not isinstance(phrase, str) or '\x00' in phrase: raise ValueError('私钥解锁口令格式错误')
    if path:
        p = Path(path).expanduser()
        if not p.is_absolute() or str(p).startswith('\\\\') or any(c in path for c in '\r\n\x00"'):
            raise ValueError('私钥路径须为本机绝对路径，不能是网络共享或包含换行、双引号')
        path = str(p)
    record.update(privateKeyPath=path, keyPassphrase=phrase)
    return record


def key_path(record, store):
    return Path(record['privateKeyPath']) if record.get('privateKeyPath') else store.root / 'keys' / record['keyName']


def load_key(record, path=None):
    try:
        phrase = record.get('keyPassphrase', '')
        return paramiko.PKey.from_path(path or record['privateKeyPath'], password=phrase.encode('utf-8') if phrase else None)
    except (OSError, ValueError, TypeError, paramiko.SSHException) as exc:
        raise ValueError('无法读取私钥，请检查文件路径、密钥格式和解锁口令') from exc


def private_material(record):
    """Only for an active job: decrypted key goes to private WSL temp storage over stdin."""
    try:
        content = Path(record['privateKeyPath']).read_bytes()
        phrase = record.get('keyPassphrase', '')
        loader = serialization.load_ssh_private_key if b'BEGIN OPENSSH PRIVATE KEY' in content else serialization.load_pem_private_key
        key = loader(content, password=phrase.encode('utf-8') if phrase else None)
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode('ascii')
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('无法读取私钥，请检查文件路径、密钥格式和解锁口令') from exc


def prepare_keys(data):
    data['keys'] = {}
    seen = {}
    for host in data['inventory']['all']['hosts'].values():
        path = host.pop('_dt_key_path', '')
        phrase = host.pop('_dt_key_passphrase', '')
        if not path: continue
        identity = (path, phrase)
        if identity not in seen:
            ref = str(len(seen))
            data['keys'][ref] = private_material(dict(privateKeyPath=path, keyPassphrase=phrase))
            seen[identity] = ref
        host['_dt_key_ref'] = seen[identity]


def login_defaults(store):
    from storage import dpapi
    path = store.root / 'login-defaults.dpapi'
    saved = json.loads(dpapi(path.read_bytes(), True)) if path.exists() else {}
    return dict(user='root', privateKeyPath=saved.get('privateKeyPath', ''), keyPassphrase='')


def save_login_defaults(store, private_key_path):
    from storage import dpapi, restrict
    value = normalize_auth(dict(privateKeyPath=private_key_path))
    path = store.root / 'login-defaults.dpapi'; temp = path.with_suffix('.tmp')
    temp.write_bytes(dpapi(json.dumps(dict(privateKeyPath=value['privateKeyPath'])).encode()))
    restrict(temp); temp.replace(path)
