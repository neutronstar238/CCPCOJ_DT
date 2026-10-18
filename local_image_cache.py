"""Reuse verified layers from tool archives and an already running local Docker."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile


class LocalLayers:
    def __init__(self, root, log):
        self.root, self.log = Path(root), log
        self.docker_attempted = set()

    def prepare(self, canonical, layers, diffs, cache):
        from registry_download import digest_hex, hash_file
        missing = {}
        for desc, diff in zip(layers, diffs):
            raw = cache / digest_hex(desc['digest']); plain = cache / (digest_hex(diff)+'.layer')
            if raw.is_file() and raw.stat().st_size == desc['size'] and hash_file(raw) == raw.name: continue
            if plain.is_file() and hash_file(plain) == diff[7:]: continue
            missing[desc['digest']] = (desc, diff)
        if not missing: return
        try: catalog = json.loads((self.root/'catalog.json').read_text(encoding='utf-8'))
        except (OSError, ValueError): catalog = {}
        self.log(f'查找已有归档中的共享镜像层：缺少 {len(missing)} 层')
        for item in reversed(catalog.get('archives', [])):
            digest = item.get('id','')
            if not re.fullmatch('[0-9a-f]{64}', digest): continue
            self.from_archive(self.root/(digest+'.tar'), missing, cache)
            if not missing: return
        self.from_docker(canonical, missing, cache)

    def from_archive(self, path, missing, cache):
        def copy_verified(stream, target, digest):
            temp = target.with_suffix('.reuse-part'); h = hashlib.sha256()
            try:
                with temp.open('wb') as out:
                    for block in iter(lambda: stream.read(4*1024**2), b''):
                        out.write(block); h.update(block)
                if h.hexdigest() != digest: return False
                temp.replace(target); return True
            finally: temp.unlink(missing_ok=True)
        try:
            with tarfile.open(path, 'r:*') as tar:
                members = {m.name.removeprefix('./'):m for m in tar if m.isfile()}
                def small_json(name):
                    m = members.get(name)
                    if not m or m.size > 4*1024**2: return None
                    return json.load(tar.extractfile(m))
                for digest, (desc, diff) in list(missing.items()):
                    m = members.get('blobs/sha256/'+digest[7:])
                    if m and m.size == desc['size']:
                        with tar.extractfile(m) as stream:
                            if copy_verified(stream, cache/digest[7:], digest[7:]):
                                del missing[digest]; self.log('复用已有归档层 '+digest[7:19])
                if not missing: return
                for entry in small_json('manifest.json') or []:
                    config = small_json(entry.get('Config','')) or {}
                    if config.get('os') != 'linux' or config.get('architecture') != 'amd64': continue
                    for name, diff in zip(entry.get('Layers',[]), config.get('rootfs',{}).get('diff_ids',[])):
                        wanted = [d for d, (_, expected) in missing.items() if expected == diff]
                        m = members.get(name)
                        if not wanted or not m: continue
                        with tar.extractfile(m) as stream:
                            magic = stream.read(2); stream.seek(0)
                            inp = gzip.GzipFile(fileobj=stream) if magic == b'\x1f\x8b' else stream
                            if copy_verified(inp, cache/(diff[7:]+'.layer'), diff[7:]):
                                for digest in wanted: del missing[digest]
                                self.log('复用已有归档文件系统层 '+diff[7:19])
        except (OSError, ValueError, tarfile.TarError, EOFError, KeyError, TypeError):
            self.log('此归档无法复用所需层，继续检查缓存或下载')

    def from_docker(self, canonical, missing, cache):
        repo = canonical.rsplit(':',1)[0]
        if repo in self.docker_attempted or not shutil.which('docker'): return
        self.docker_attempted.add(repo)
        flags = 0x08000000 if os.name == 'nt' else 0
        def run(args, timeout=10):
            return subprocess.run(['docker']+args, capture_output=True, text=True,
                                  timeout=timeout, check=True, creationflags=flags).stdout
        try:
            # Never contact a remote Docker context, start Docker, or pull here.
            context = os.environ.get('DOCKER_CONTEXT')
            host = '' if context else os.environ.get('DOCKER_HOST','')
            if not host:
                host = json.loads(run(['context','inspect']+([context] if context else [])))[0]['Endpoints']['docker']['Host']
            if not host.startswith(('npipe://','unix://')): return
            rows = [json.loads(line) for line in run(['image','ls','--format','{{json .}}']).splitlines()]
            refs = list(dict.fromkeys(x['Repository']+':'+x['Tag'] for x in rows
                        if x['Tag'] != '<none>' and (x['Repository'] == repo or x['Repository'].endswith('/'+repo))))[:3]
            for ref in refs:
                size = int(run(['image','inspect',ref,'--format','{{.Size}}']))
                if shutil.disk_usage(self.root).free < size*2 + 64*1024**2: break
                self.log('复用本机 Docker 已有镜像层：'+ref+'（仅本地导出）')
                with tempfile.TemporaryDirectory(prefix='reuse-', dir=self.root) as temp:
                    archive = Path(temp)/'image.tar'
                    run(['image','save','--output',str(archive),ref], timeout=180)
                    self.from_archive(archive, missing, cache)
                if not missing: break
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            self.log('本机 Docker 缓存暂不可读，继续使用归档缓存和网络下载')
