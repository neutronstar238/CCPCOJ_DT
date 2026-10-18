"""Download public OCI images on the Windows host, independent of Docker Desktop."""
import gzip
import concurrent.futures
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import time
import threading
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json'])


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Some registries return HTTP CDN locations. Never send tokens over HTTP
        # or forward registry credentials to a different CDN origin.
        if newurl.startswith('http://'): newurl = 'https://' + newurl[7:]
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('镜像下载重定向必须使用 HTTPS')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            result.remove_header('Authorization')
        return result


def is_http_proxy(port):
    # VPN control/metrics listeners are not proxy endpoints. In particular this
    # installation's 53000 listener is a control API, never a download route.
    if port == 53000: return False
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2) as sock:
            sock.sendall(b'CONNECT registry-1.docker.io:443 HTTP/1.1\r\nHost: registry-1.docker.io:443\r\n\r\n')
            line = b''
            while b'\r\n' not in line and len(line) < 1024:
                block = sock.recv(256)
                if not block: break
                line += block
            return bool(re.match(rb'HTTP/1\.[01] 200(?: |\r)', line))
    except OSError: return False


def network_routes():
    """Use configured proxies or HTTP listeners owned by known local VPN apps."""
    routes = []
    system = urllib.request.getproxies()
    system = {key:value for key,value in system.items()
              if key not in ('http','https') or not re.search(r'(?:localhost|127\.0\.0\.1):53000(?:/|$)',value)}
    if system.get('https') or system.get('http'):
        routes.append(('系统代理', system))
    if os.name == 'nt':
        script = "$ids=@(Get-Process -ErrorAction SilentlyContinue | Where-Object {$_.ProcessName -match '^(iKuuuVPNCore|mihomo|clash|clash-core|clash-core-service|v2ray|sing-box)$'} | Select-Object -ExpandProperty Id); @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.OwningProcess -in $ids -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0')} | Select-Object -ExpandProperty LocalPort -Unique) | ConvertTo-Json -Compress"
        try:
            p = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                capture_output=True, timeout=15, creationflags=0x08000000)
            ports = json.loads(p.stdout.decode('utf-8-sig') or '[]')
            for port in sorted(ports if isinstance(ports, list) else [ports]):
                if isinstance(port, int) and 0 < port < 65536 and port != 53000 and is_http_proxy(port):
                    proxy = f'http://127.0.0.1:{port}'
                    routes.append((f'本机代理 127.0.0.1:{port}', dict(http=proxy, https=proxy)))
        except (OSError, ValueError, subprocess.TimeoutExpired): pass
    routes.append(('直连', {}))
    return routes


def digest_hex(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('镜像仅支持 SHA256 内容摘要')
    return value[7:]


def hash_file(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


class Registry:
    def __init__(self, source, proxies, log):
        first, _, rest = source.partition('/')
        if rest and ('.' in first or ':' in first or first == 'localhost'):
            host, image = first, rest
        else:
            host, image = 'registry-1.docker.io', source
            if '/' not in image: image = 'library/' + image
        self.repo, self.tag = image.rsplit(':', 1)
        self.base = f'https://{host}/v2/{self.repo}'
        self.proxies = proxies
        self.local = threading.local()
        self.token = None
        self.log = log

    @property
    def opener(self):
        if not hasattr(self.local, 'opener'):
            self.local.opener = urllib.request.build_opener(urllib.request.ProxyHandler(self.proxies), SafeRedirect())
        return self.local.opener

    def open(self, suffix):
        headers = {'Accept': ACCEPT, 'User-Agent': 'CSGOJDeploymentConsole/1.9.2'}
        if self.token: headers['Authorization'] = 'Bearer ' + self.token
        try:
            return self.opener.open(urllib.request.Request(self.base + suffix, headers=headers), timeout=20)
        except urllib.error.HTTPError as e:
            if e.code != 401: raise
            challenge = e.headers.get('WWW-Authenticate', '')
            e.close()
            if not challenge.lower().startswith('bearer '): raise ValueError('仓库需要不支持的登录认证')
            fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = fields.get('realm', '')
            if urllib.parse.urlsplit(realm).scheme != 'https': raise ValueError('仓库令牌地址必须使用 HTTPS')
            query = urllib.parse.urlencode(dict(service=fields.get('service', ''), scope=f'repository:{self.repo}:pull'))
            with self.opener.open(realm + ('&' if '?' in realm else '?') + query, timeout=20) as response:
                data = json.loads(response.read(4 * 1024**2))
            self.token = data.get('token') or data.get('access_token')
            if not self.token: raise ValueError('仓库未返回下载令牌')
            headers['Authorization'] = 'Bearer ' + self.token
            return self.opener.open(urllib.request.Request(self.base + suffix, headers=headers), timeout=20)

    def manifest(self, ref, expected=None):
        for attempt in range(3):
            try:
                with self.open('/manifests/' + urllib.parse.quote(ref, safe=':')) as response:
                    raw = response.read(4 * 1024**2 + 1)
                    header_digest = response.headers.get('Docker-Content-Digest')
                if len(raw) > 4 * 1024**2: raise ValueError('镜像清单过大')
                for check in (expected, header_digest):
                    if check and hashlib.sha256(raw).hexdigest() != digest_hex(check):
                        raise ValueError('镜像清单 SHA256 不一致')
                return json.loads(raw)
            except (OSError, TimeoutError, http.client.HTTPException) as e:
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404): raise
                if attempt == 2: raise
                self.log(f'清单请求中断，自动重试 {attempt+1}/2')
                time.sleep(1)

    def blob(self, desc, cache):
        digest = digest_hex(desc['digest']); size = desc['size']
        if not isinstance(size, int) or size < 0: raise ValueError('镜像文件大小无效')
        target = cache / digest
        if target.is_file() and target.stat().st_size == size and hash_file(target) == digest:
            self.log(f'复用已校验镜像层 {digest[:12]}'); return target
        if shutil.disk_usage(cache).free < size + 64*1024**2: raise ValueError('本地镜像缓存磁盘空间不足')
        partial = target.with_suffix('.part')
        for attempt in range(3):
            try:
                h = hashlib.sha256(); done = 0; last = time.monotonic()
                with self.open('/blobs/' + desc['digest']) as response, partial.open('wb') as out:
                    while block := response.read(1024*1024):
                        out.write(block); h.update(block); done += len(block)
                        if done > size: raise ValueError('镜像文件超出清单大小')
                        if time.monotonic()-last >= 2:
                            self.log(f'下载层 {digest[:12]}：{done/1048576:.1f}/{size/1048576:.1f} MB（{done*100/max(size,1):.0f}%）'); last=time.monotonic()
                if done != size or h.hexdigest() != digest: raise ValueError('镜像层 SHA256 或大小不一致')
                partial.replace(target); self.log(f'镜像层校验通过 {digest[:12]}（{size/1048576:.1f} MB）'); return target
            except (OSError, TimeoutError, ValueError, http.client.HTTPException):
                if attempt == 2: raise
                self.log(f'镜像层 {digest[:12]} 中断或校验失败，自动重试 {attempt+1}/2'); time.sleep(1)
            finally: partial.unlink(missing_ok=True)

    def download(self, canonical, cache, output, reuse=None):
        manifest = self.manifest(self.tag)
        for _ in range(4):
            if 'manifests' not in manifest: break
            candidates = [d for d in manifest['manifests'] if d.get('platform', {}).get('os') == 'linux'
                and d.get('platform', {}).get('architecture') == 'amd64']
            if len(candidates) != 1: raise ValueError('没有唯一的 Linux amd64 镜像，未改用其他平台')
            ref = candidates[0]['digest']; digest_hex(ref)
            manifest = self.manifest(ref, ref)
        if manifest.get('schemaVersion') != 2 or 'config' not in manifest or 'layers' not in manifest:
            raise ValueError('镜像清单格式不支持')
        conf_path = self.blob(manifest['config'], cache)
        conf = json.loads(conf_path.read_bytes())
        if conf.get('os') != 'linux' or conf.get('architecture') != 'amd64': raise ValueError('镜像平台不是 Linux amd64')
        layers = manifest['layers']; diff_ids = conf.get('rootfs', {}).get('diff_ids', [])
        if len(layers) != len(diff_ids): raise ValueError('镜像配置与层数不一致')
        if reuse: reuse.prepare(canonical, layers, diff_ids, cache)
        unique = {}
        for desc, diff in zip(layers, diff_ids):
            digest_hex(desc['digest']); digest_hex(diff)
            if desc['digest'] in unique and unique[desc['digest']][1] != diff:
                raise ValueError('同一镜像层对应不同 diff_id')
            unique[desc['digest']] = (desc, diff)
        self.log(f'最多 4 层并发下载与校验；共 {len(layers)} 层，{len(unique)} 个不同镜像层')

        def prepare_layer(n, desc, diff):
            self.log(f'镜像层 {n}/{len(layers)}')
            digest = digest_hex(diff); layer = cache / (digest+'.layer')
            if layer.is_file() and hash_file(layer) == digest:
                self.log(f'复用本地未压缩镜像层 {digest[:12]}，无需下载')
                media = ('application/vnd.docker.image.rootfs.diff.tar' if 'docker' in desc.get('mediaType','')
                         else 'application/vnd.oci.image.layer.v1.tar')
                return layer, 'blobs/sha256/'+digest, dict(mediaType=media,digest=diff,size=layer.stat().st_size)
            raw = self.blob(desc, cache)
            media = desc.get('mediaType', '')
            if not media.endswith(('.tar', '.tar.gzip', '.tar+gzip')): raise ValueError('不支持此镜像层压缩格式：'+media)
            # Verify the uncompressed digest in memory; do not write every layer
            # to disk a second time merely to hash it.
            with (gzip.open(raw, 'rb') if media.endswith('gzip') else raw.open('rb')) as inp:
                h = hashlib.file_digest(inp, 'sha256')
            if h.hexdigest() != digest: raise ValueError('解压后的镜像层 diff_id 校验失败')
            return raw, 'blobs/sha256/'+raw.name, desc

        prepared = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(prepare_layer, n, desc, diff): digest
                       for n, (digest, (desc, diff)) in enumerate(unique.items(), 1)}
            try:
                for future in concurrent.futures.as_completed(futures):
                    prepared[futures[future]] = future.result()
            except Exception:
                for future in futures: future.cancel()
                raise
        results = [prepared[d['digest']] for d in layers]
        files = [(path,name) for path,name,_ in results]
        manifest['layers'] = [desc for _,_,desc in results]
        config_name = 'blobs/sha256/' + conf_path.name
        docker_manifest = [dict(Config=config_name, RepoTags=[canonical], Layers=[name for _, name in files])]
        total = sum(p.stat().st_size for p, _ in files) + conf_path.stat().st_size
        if shutil.disk_usage(output.parent).free < total*2 + 64*1024**2: raise ValueError('生成及登记镜像归档磁盘空间不足')
        self.log('全部镜像层校验通过，正在生成 Docker 可导入归档…')
        # OCI metadata preserves a verifiable manifest identity on containerd-backed
        # Docker; manifest.json also supports classic Docker Engine import.
        manifest_raw = json.dumps(manifest, separators=(',', ':')).encode()
        manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
        index = dict(schemaVersion=2, manifests=[dict(
            mediaType=manifest.get('mediaType', 'application/vnd.oci.image.manifest.v1+json'),
            digest='sha256:'+manifest_digest, size=len(manifest_raw),
            annotations={'io.containerd.image.name': 'docker.io/'+(canonical if '/' in canonical else 'library/'+canonical),
                         'org.opencontainers.image.ref.name': canonical},
            platform=dict(os='linux', architecture='amd64'))])
        with tarfile.open(output, 'w') as tar:
            tar.add(conf_path, arcname=config_name)
            for name, path in dict((name,path) for path,name in files).items():
                tar.add(path, arcname=name)
            for name, raw in [('manifest.json', json.dumps(docker_manifest).encode()),
                              ('blobs/sha256/'+manifest_digest, manifest_raw),
                              ('index.json', json.dumps(index).encode()),
                              ('oci-layout', b'{"imageLayoutVersion":"1.0.0"}')]:
                member=tarfile.TarInfo(name); member.size=len(raw)
                tar.addfile(member, io.BytesIO(raw))


def download_image(canonical, sources, root, output, log, reuse=None):
    cache = root / 'blobs'; cache.mkdir(exist_ok=True)
    routes = network_routes(); failures = []
    for source in sources:
        for label, proxies in routes:
            log(f'自动下载：{source}；通道：{label}')
            try:
                Registry(source, proxies, log).download(canonical, cache, output, reuse=reuse)
                log('实际下载源：'+source+'；已完成 Linux amd64 内容校验')
                return source
            except Exception as e:
                # Avoid echoing signed redirect URLs, proxy credentials or bearer tokens.
                if isinstance(e, urllib.error.HTTPError):
                    reason = {404:'该源未找到此仓库或版本（HTTP 404）', 429:'仓库限流（HTTP 429）',
                        401:'仓库认证失败（HTTP 401）', 403:'仓库拒绝访问（HTTP 403）'}.get(e.code, f'仓库返回 HTTP {e.code}')
                elif isinstance(e, ValueError): reason = str(e)
                elif isinstance(e, (TimeoutError, urllib.error.URLError)):
                    reason = '网络连接失败或超时，请检查此通道的联网状态'
                elif isinstance(e, http.client.HTTPException): reason = '网络传输中断，未收到完整内容'
                elif isinstance(e, OSError): reason = f'本机文件或网络操作失败（错误码 {getattr(e,"winerror",None) or e.errno}）'
                else: reason = type(e).__name__
                log(f'此通道失败：{reason}；自动尝试下一通道或下载源')
                failures.append(f'{source}（{label}）：{reason}')
                output.unlink(missing_ok=True)
                if isinstance(e, urllib.error.HTTPError) and e.code == 404: break
    raise RuntimeError('所有下载源和本机网络通道均失败；已校验镜像层保留供重试。\n'+'\n'.join(failures))
