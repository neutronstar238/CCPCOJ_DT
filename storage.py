"""Windows-user DPAPI vault; no credentials in the distribution directory."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

def dpapi(data, decrypt=False):
    if os.name != 'nt': raise RuntimeError('凭据库仅支持 Windows DPAPI')
    buf = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))); out = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError()
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def restrict(path):
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami'], creationflags=0x08000000, text=True).strip()
        grant = identity + (':(OI)(CI)F' if Path(path).is_dir() else ':F')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', grant, '*S-1-5-18:F'], check=True, capture_output=True, creationflags=0x08000000)
    else: Path(path).chmod(0o700 if Path(path).is_dir() else 0o600)

class Store:
    def __init__(self, root=None):
        self.root = Path(root or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CSGOJDeploy')
        self.root.mkdir(parents=True, exist_ok=True); restrict(self.root)
        for d in ['keys', 'backups', 'tools', 'logs']: (self.root / d).mkdir(exist_ok=True)
    def save(self, config):
        target = self.root / 'config.dpapi'
        data = dpapi(json.dumps(config, ensure_ascii=False).encode())
        tmp = target.with_suffix('.tmp'); tmp.write_bytes(data); restrict(tmp); tmp.replace(target)
    def load(self):
        p = self.root / 'config.dpapi'
        if not p.exists(): return None
        return json.loads(dpapi(p.read_bytes(), True))
    def key(self, name):
        import re
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', name): raise ValueError('私钥名称不合法')
        p = self.root / 'keys' / name
        if not p.exists():
            key = Ed25519PrivateKey.generate()
            with p.open('xb') as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
            restrict(p)
        key = serialization.load_ssh_private_key(p.read_bytes(), password=None)
        pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() + ' ' + name
        p.with_suffix('.pub').write_text(pub + '\n', encoding='ascii')
        return p, pub
    def tool(self, data, name=None):
        import hashlib
        if len(data) < 64 or len(data) > 40 * 1024 * 1024 or data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\x3e\x00':
            raise ValueError('请选择 Linux x86_64 ELF 部署程序，大小不超过 40 MB')
        digest = hashlib.sha256(data).hexdigest()
        p = self.root / 'tools' / (digest + '.bin'); p.write_bytes(data)
        result = dict(sha256=digest, size=len(data), localPath=str(p.resolve()))
        if name:
            import re
            name = str(name).replace('\\', '/').rsplit('/', 1)[-1][:240]
            result.update(name=name, source='local')
            match = re.fullmatch(r'csgoj-deploy[-_](v?\d+\.\d+\.\d+(?:[-.][A-Za-z0-9_.-]+)?)[-_]linux[-_]amd64', name)
            if match: result.update(version=match.group(1), versionSource='filename')
        return result
