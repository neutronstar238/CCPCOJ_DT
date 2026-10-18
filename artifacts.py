"""Release discovery and immutable local Docker-save archives. No server-side pulls."""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import quote, urlsplit
import uuid

REPOSITORY = 'SZTU-ACM/CCPCOJ-2'
TENCENT = 'ccr.ccs.tencentyun.com/csgrandeur'
DEPENDENCIES = ['mysql:8.0.32', 'nginx:1.29.7-alpine', 'phpmyadmin:5.2.3']
VERSION = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}'

def request(url):
    return urllib.request.Request(url, headers={'User-Agent': 'CSGOJDeploymentConsole/1.7'})

def fetch_json(url):
    with urllib.request.urlopen(request(url), timeout=30) as response:
        return json.load(response)

def release_assets(release):
    return [a for a in release.get('assets', []) if re.fullmatch(
        r'csgoj-deploy(?:[-_].*?)?[-_]linux[-_](?:amd64|x86_64)', a['name'], re.I)]

def releases(log=lambda _: None):
    rows = []
    for page in range(1, 1001):
        log(f'正在读取 Release 第 {page} 页')
        batch = fetch_json(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100&page={page}')
        for r in batch:
            if not r.get('draft'):
                rows.append(dict(version=r['tag_name'], prerelease=r.get('prerelease', False),
                    assets=[dict(name=a['name'], size=a['size']) for a in release_assets(r)]))
        if len(batch) < 100: return dict(releases=rows, source=f'https://github.com/{REPOSITORY}/releases')
    raise ValueError('Release 页数超出上限，未返回不完整列表')

def download_tool(store, version, asset=None, log=lambda _: None):
    if not re.fullmatch(VERSION, version): raise ValueError('Release 标签格式错误')
    try:
        release = fetch_json(f'https://api.github.com/repos/{REPOSITORY}/releases/tags/{quote(version, safe="")}')
    except urllib.error.HTTPError as e:
        if e.code == 404: raise ValueError(f'Release {version} 不存在；没有改下载其他版本。') from e
        raise
    candidates = release_assets(release)
    if asset: candidates = [a for a in candidates if a['name'] == asset]
    if len(candidates) != 1: raise ValueError('该 Release 没有唯一的 Linux amd64 部署附件，请选择具体附件；没有改下载其他版本。')
    a = candidates[0]; source = a['browser_download_url']
    if not source.startswith(f'https://github.com/{REPOSITORY}/releases/download/'):
        raise ValueError('附件下载地址不属于官方仓库')
    log('下载部署程序：' + a['name'])
    data = bytearray()
    with urllib.request.urlopen(request(source), timeout=90) as response:
        while True:
            block = response.read(256 * 1024)
            if not block: break
            data.extend(block)
            if len(data) > 40 * 1024**2: raise ValueError('部署程序超过 40 MB')
            log(f'部署程序已下载 {len(data)/1048576:.1f} / {a["size"]/1048576:.1f} MB')
    if len(data) != a['size']: raise ValueError('部署程序下载不完整')
    if (a.get('digest') or '').startswith('sha256:') and hashlib.sha256(data).hexdigest() != a['digest'][7:]:
        raise ValueError('Release 附件 SHA256 不一致')
    result = store.tool(bytes(data))
    result.update(version=version, name=a['name'], source=source)
    return result

def image_tags(kind, log=lambda _: None):
    if kind not in ('web', 'judge'): raise ValueError('镜像类型错误')
    repo = 'ccpcoj-web2' if kind == 'web' else 'ccpcoj-judge2'
    address = f'https://hub.docker.com/v2/repositories/csgrandeur/{repo}/tags?page_size=100&ordering=last_updated'
    source = address; seen = set(); rows = {}; page = 0
    while address:
        if address in seen or urlsplit(address).hostname != 'hub.docker.com' or urlsplit(address).scheme != 'https':
            raise ValueError('镜像版本分页地址无效')
        seen.add(address); page += 1
        if page > 1000: raise ValueError('镜像版本页数超限，未返回不完整列表')
        log(f'正在读取镜像标签第 {page} 页')
        data = fetch_json(address)
        for x in data['results']:
            if re.fullmatch(VERSION, x['name']):
                rows[x['name']] = dict(name=x['name'], updated=x.get('last_updated'))
        address = data.get('next')
    return dict(tags=list(rows.values()), source=source, notice=f'已查询全部 {len(rows)} 个标签；仓库返回顺序。腾讯云源可能尚未同步部分 exp 标签，下载会优先使用所选源，失败后回退其他源，全部失败会明确报错。')

def references(kind, version, dependencies=False):
    if kind not in ('web', 'judge', 'both') or not re.fullmatch(VERSION, version): raise ValueError('镜像类型或标签错误')
    refs = ['csgrandeur/ccpcoj-' + k + '2:' + version for k in ('web', 'judge') if kind in (k, 'both')]
    if dependencies and kind in ('web', 'both'): refs += DEPENDENCIES
    return refs

def pull_reference(ref, mode, mirror=''):
    if mode == 'hub': return ref
    if mode == 'tencent': return TENCENT + '/' + ref.split('/')[-1]
    if mode != 'custom': raise ValueError('镜像源类型错误')
    u = urlsplit(mirror if '://' in mirror else 'https://' + mirror)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path.rstrip('/') or u.query or u.fragment:
        raise ValueError('自定义镜像源应为 HTTPS 域名，可含端口，不含路径或密码')
    # Docker Hub proxy registries retain the library namespace for official images.
    return u.netloc + '/' + (ref if '/' in ref else 'library/' + ref)

def file_hash(path, progress=lambda *_: None):
    h = hashlib.sha256(); done = 0; size = Path(path).stat().st_size
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block); done += len(block); progress(done, size)
    return h.hexdigest()

def pull_with_fallback(ref, mode, mirror, run, log):
    """Try the preferred registry first, keeping the exact tag and platform."""
    modes = [mode] + [m for m in ('tencent', 'hub') if m != mode]
    sources = list(dict.fromkeys(pull_reference(ref, m, mirror) for m in modes))
    failures = []
    for i, source in enumerate(sources, 1):
        log(f'拉取尝试 {i}/{len(sources)}：{source}')
        try:
            run(['pull', '--platform', 'linux/amd64', source])
        except (RuntimeError, TimeoutError) as e:
            failures.append(source + '：' + str(e))
            log(f'源失败：{source}；{e}')
            if i < len(sources): log('正在回退到下一个源，保持相同版本标签…')
        else:
            log('实际下载源：' + source)
            return source
    raise RuntimeError('所有下载源均失败，未生成此镜像的归档：' + ref + '\n' + '\n'.join(failures))

def archive_images(path, identities=None):
    """Read manifest/config only; never extract archive members onto the host."""
    with tarfile.open(path, 'r:*') as tar:
        members = {}
        for member in tar:
            name = member.name.removeprefix('./')
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or member.issym() or member.islnk() or member.isdev():
                raise ValueError('镜像归档含不安全的路径或链接')
            if name in members: raise ValueError('镜像归档包含重复路径')
            members[name] = member
        def read_json(name):
            m = members.get(name)
            if not m or not m.isfile() or m.size > 4 * 1024**2: raise ValueError('镜像清单或配置缺失/过大')
            return json.load(tar.extractfile(m))
        manifest = read_json('manifest.json'); images = {}
        if not isinstance(manifest, list) or not manifest: raise ValueError('请选择 docker save 生成的镜像归档，不能使用 docker export 容器文件')
        for entry in manifest:
            conf = read_json(entry['Config'])
            if conf.get('os') != 'linux' or conf.get('architecture') != 'amd64':
                raise ValueError('仅支持 Linux amd64 镜像，归档中包含其他平台')
            for layer in entry['Layers']:
                if layer not in members or not members[layer].isfile(): raise ValueError('镜像归档缺失 layer 文件')
            image_id = 'sha256:' + hashlib.sha256(tar.extractfile(members[entry['Config']]).read()).hexdigest()
            for ref in entry.get('RepoTags') or []:
                if not re.fullmatch(r'[a-z0-9][a-z0-9./_-]*:' + VERSION, ref): raise ValueError('归档镜像标签格式错误')
                if ref in images and images[ref] != image_id: raise ValueError('归档中同一标签对应不同镜像')
                images[ref] = image_id
        if not images: raise ValueError('镜像归档没有 RepoTags，请先给镜像加标签再 docker save')
        # Classic Docker uses config digests as IDs; containerd-backed Docker uses
        # OCI manifest/index digests. Only accept ancestors of this exact config.
        if identities is not None:
            identities.update({ref:[iid] for ref,iid in images.items()})
            if 'index.json' in members:
                def ancestors(desc, config_id, depth=0):
                    if depth > 8: raise ValueError('OCI index 嵌套过深')
                    digest=desc.get('digest','')
                    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest): return []
                    name='blobs/sha256/'+digest[7:]
                    if name not in members: return []
                    m=members[name]
                    if not m.isfile() or m.size>4*1024**2: return []
                    raw=tar.extractfile(m).read()
                    if hashlib.sha256(raw).hexdigest()!=digest[7:]: raise ValueError('OCI manifest 校验失败')
                    obj=json.loads(raw)
                    if obj.get('config',{}).get('digest')==config_id: return [digest]
                    result=[]
                    for child in obj.get('manifests',[]): result+=ancestors(child,config_id,depth+1)
                    return result+[digest] if result else []
                for desc in read_json('index.json').get('manifests',[]):
                    ref=desc.get('annotations',{}).get('io.containerd.image.name','').removeprefix('docker.io/')
                    if ref.startswith('library/'): ref=ref[8:]
                    if ref in images: identities[ref]+=ancestors(desc,images[ref])
        return images

class ImageCache:
    def __init__(self, store):
        self.root = store.root / 'images'; self.root.mkdir(exist_ok=True)
        self.index = self.root / 'catalog.json'
    def catalog(self):
        return json.loads(self.index.read_text(encoding='utf-8')) if self.index.exists() else dict(archives=[], selected={})
    def describe(self):
        data = self.catalog()
        for a in data['archives']:
            path = self.root / (a['id'] + '.tar')
            a['available'] = path.is_file() and path.stat().st_size == a['size']
        data.update(cacheDir=str(self.root), indexPath=str(self.index), dependencies=DEPENDENCIES)
        return data
    def write(self, data):
        tmp = self.index.with_suffix('.tmp'); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'); tmp.replace(self.index)
    def import_file(self, path, log=lambda _: None):
        src = Path(path)
        if not src.is_file(): raise ValueError('本地镜像文件不存在')
        if shutil.disk_usage(self.root).free < src.stat().st_size + 64*1024**2: raise ValueError('本地镜像缓存磁盘空间不足')
        tmp = self.root / (uuid.uuid4().hex + '.part')
        try:
            size = src.stat().st_size; done = 0; last = -1; h = hashlib.sha256()
            with src.open('rb') as f, tmp.open('xb') as out:
                for block in iter(lambda: f.read(4*1024**2), b''):
                    out.write(block); h.update(block); done += len(block)
                    percent = int(done*100/max(size, 1))
                    if percent//5 != last: log(f'复制本地镜像 {percent}%（{done/1048576:.0f}/{size/1048576:.0f} MB）'); last = percent//5
            log('正在检查归档的镜像标签、平台和完整性…')
            identities = {}; images = archive_images(tmp, identities); digest = h.hexdigest(); dest = self.root / (digest + '.tar')
            tmp.replace(dest)
            item = dict(id=digest, sha256=digest, path=str(dest), name=src.name, size=done, images=images, identities=identities, created=time.time())
            c = self.catalog(); c['archives'] = [a for a in c['archives'] if a['id'] != digest] + [item]
            c['selected'].update({ref:digest for ref in images}); self.write(c)
            return item
        finally: tmp.unlink(missing_ok=True)
    def select(self, digest):
        c = self.catalog(); a = next((a for a in c['archives'] if a['id'] == digest), None)
        if not a: raise ValueError('镜像缓存不存在')
        c['selected'].update({ref:digest for ref in a['images']}); self.write(c); return a
    def delete(self, digest, confirmed=False):
        if confirmed is not True: raise ValueError('请先确认删除本地归档')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('归档标识无效')
        before = self.catalog()
        item = next((a for a in before['archives'] if a['id'] == digest), None)
        if not item: raise ValueError('归档已不存在，请刷新缓存')
        # Never trust a path supplied by the request or the catalog.
        path = self.root / (digest + '.tar')
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('归档路径异常，拒绝删除')
        after = copy.deepcopy(before)
        after['archives'] = [a for a in after['archives'] if a['id'] != digest]
        cleared = [ref for ref, value in after['selected'].items() if value == digest]
        after['selected'] = {ref:value for ref,value in after['selected'].items() if value != digest}
        pending = self.root / (uuid.uuid4().hex + '.delete-pending')
        moved = path.exists()
        if moved: path.rename(pending)
        try:
            self.write(after)
        except Exception:
            if moved: pending.rename(path)
            raise
        try:
            if moved: pending.unlink()
        except Exception:
            pending.rename(path); self.write(before)
            raise
        return dict(deleted=digest, cleared=cleared, fileRemoved=moved)
    def resolve(self, refs):
        c = self.catalog(); archives = {a['id']:a for a in c['archives']}; chosen = {}
        for ref in refs:
            a = archives.get(c['selected'].get(ref))
            if not a or ref not in a['images'] or not (self.root/(a['id']+'.tar')).is_file():
                raise ValueError('本地缺少镜像 '+ref+'；请先在“版本与镜像分发”下载或导入')
            a = copy.deepcopy(a); a['path'] = str(self.root/(a['id']+'.tar')); chosen[a['id']] = a
        # docker load imports all tags: reject bundles that would overwrite another chosen snapshot.
        versions = {}
        for a in chosen.values():
            for ref, iid in a['images'].items():
                if ref in versions and versions[ref] != iid: raise ValueError('所选归档有同名标签的不同内容，请使用不冲突的归档')
                versions[ref] = iid
        return list(chosen.values())
    def pull(self, refs, mode, mirror, log):
        from registry_download import download_image
        from local_image_cache import LocalLayers
        reuse = LocalLayers(self.root, log)
        modes = [mode] + [m for m in ('tencent', 'hub') if m != mode]
        result = []
        for ref in refs:
            sources = list(dict.fromkeys(pull_reference(ref, m, mirror) for m in modes))
            tmp = self.root / (uuid.uuid4().hex + '.save.tar')
            try:
                source = download_image(ref, sources, self.root, tmp, log, reuse=reuse)
                result.append(self.import_file(tmp, log))
                log('已登记到本地缓存：' + result[-1]['path'])
            finally: tmp.unlink(missing_ok=True)
        return result


class ArtifactJobs:
    def __init__(self, store):
        self.store=store; self.cache=ImageCache(store); self.running=False; self.lock=threading.Lock()
        self.state=dict(running=False, logs=[], status='等待', result=None, job=None)
    def status(self):
        with self.lock: return copy.deepcopy(self.state)
    def log(self, value):
        with self.lock: self.state['logs']=(self.state['logs']+[str(value)])[-500:]
    def start(self, action, data):
        if action not in ('releases', 'tags', 'tool', 'import', 'pull', 'discover', 'docker-export'): raise ValueError('资源操作错误')
        with self.lock:
            if self.running: raise ValueError('资源任务仍在执行')
            self.running=True; self.state=dict(running=True, logs=[], status='执行中', result=None, job=uuid.uuid4().hex, action=action)
        def worker():
            try:
                if action=='discover':
                    from image_discovery import discover
                    result=discover(self.store, log=self.log)
                elif action=='docker-export':
                    from image_discovery import export_docker
                    result=export_docker(self.cache, data.get('ref'), data.get('imageId'), self.log)
                elif action=='releases': result=releases(self.log)
                elif action=='tags': result=image_tags(data['kind'], self.log)
                elif action=='tool':
                    result=download_tool(self.store, data['version'], data.get('asset'), self.log)
                    from models import default_config
                    config=self.store.load() or default_config()
                    config.update(tool=result, toolVersion=result['version']); self.store.save(config)
                elif action=='pull': result=self.cache.pull(references(data['kind'], data['version'], data.get('dependencies', False)), data.get('mode', 'hub'), data.get('mirror',''), self.log)
                else:
                    path=data.get('path')
                    if not path:
                        script="Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='Docker archives|*.tar;*.tar.gz;*.tgz|All files|*.*'; if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Console]::Write($d.FileName)}"
                        p=subprocess.run(['powershell.exe','-NoProfile','-STA','-Command',script],capture_output=True,timeout=300,creationflags=0x08000000)
                        if p.returncode: raise ValueError('本地文件选择器打开失败，请填写文件完整路径')
                        path=p.stdout.decode('utf-8-sig').strip()
                    if not path: raise ValueError('已取消选择镜像文件')
                    result=self.cache.import_file(path,self.log)
                with self.lock: self.state.update(status='完成',result=result)
            except Exception as e:
                self.log(str(e))
                with self.lock: self.state['status']='失败：'+str(e)
            finally:
                with self.lock: self.running=False; self.state['running']=False
        threading.Thread(target=worker,daemon=False).start()
        return self.state['job']
