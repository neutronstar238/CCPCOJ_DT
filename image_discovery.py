"""Discover local archives and local Docker images, without pulling or deploying."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

OJ_RE = re.compile(r'csgrandeur/ccpcoj-(?:web|judge)2:[A-Za-z0-9_.-]+$')
DEPENDENCY_RE = re.compile(r'(?:mysql|nginx|phpmyadmin):[A-Za-z0-9_.-]+$')

def supported_ref(ref):
    return isinstance(ref, str) and bool(OJ_RE.fullmatch(ref) or DEPENDENCY_RE.fullmatch(ref))

def docker_run(args, timeout=20):
    executable = shutil.which('docker')
    if not executable:
        raise ValueError('未找到 Docker CLI，请安装 Docker Desktop，或手动选择镜像归档')
    # Pin the local endpoint: ignore remote Docker contexts and host environment.
    endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
    environment = os.environ.copy()
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        environment.pop(key, None)
    try:
        result = subprocess.run([executable, '--host', endpoint] + args,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, env=environment, creationflags=0x08000000 if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:
        raise ValueError('本机 Docker 响应超时，请确认 Docker Desktop 已启动') from None
    if result.returncode:
        prefix = '本机 Docker 不可用，请启动 Docker Desktop 并等待引擎就绪。' if any(word in result.stderr.lower() for word in ('failed to connect','cannot find','connection refused')) else 'Docker 操作失败，未生成可用归档：'
        raise ValueError(prefix+result.stderr.strip()[-400:])
    return result.stdout

def docker_inventory():
    try:
        rows = [json.loads(line) for line in docker_run(['image', 'ls', '--format', '{{json .}}']).splitlines() if line.strip()]
        candidates=[]
        for row in rows:
            repo=row.get('Repository', '').removeprefix('docker.io/')
            ref=repo+':'+row.get('Tag', '')
            if supported_ref(ref): candidates.append(ref)
        refs=sorted(set(candidates))
        # Inspect the actual image configuration; filenames and display tags alone
        # do not establish platform or immutable identity.
        images=[]; skipped=[]
        if refs:
            try:
                inspected=json.loads(docker_run(['image', 'inspect', '--platform', 'linux/amd64']+refs))
            except ValueError:
                # One ARM-only or concurrently removed tag must not hide valid images.
                inspected=[]
                for ref in refs:
                    try: inspected.extend(json.loads(docker_run(['image','inspect','--platform','linux/amd64',ref])))
                    except ValueError as error: skipped.append(ref+'：'+str(error))
            for image in inspected:
                if image.get('Os')!='linux' or image.get('Architecture')!='amd64': continue
                for tag in image.get('RepoTags') or []:
                    ref=tag.removeprefix('docker.io/')
                    if supported_ref(ref):
                        images.append(dict(ref=ref, id=image['Id'], size=image.get('Size', 0), platform='linux/amd64'))
        return dict(available=True, images=images, message='本机 Docker 已连接；只读取现有镜像，不拉取、不部署。'+(' 跳过：'+'；'.join(skipped) if skipped else ''))
    except (OSError, ValueError, KeyError, TypeError) as error:
        return dict(available=False, images=[], message=str(error))

def discover(store, roots=None, log=lambda _:None):
    from artifacts import archive_images
    package=Path(sys.executable).resolve().parent.parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    roots=roots if roots is not None else [package/'images', store.root/'images', Path.home()/'Downloads', Path.home()/'Desktop', Path.home()/'Documents'/'Codex']
    found, rejected, seen=[], [], set()
    truncated=False
    for root in roots:
        root=Path(root)
        if not root.is_dir(): continue
        for folder, dirs, files in os.walk(root, followlinks=False):
            relative=Path(folder).relative_to(root)
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', '_internal', 'backups', 'source', 'app-original', 'build-temp')) if len(relative.parts)<6 else []
            for name in sorted(files):
                if not name.lower().endswith(('.tar', '.tar.gz', '.tgz')): continue
                path=(Path(folder)/name).resolve()
                if not path.is_relative_to(root.resolve()): continue
                key=str(path).casefold()
                if key in seen: continue
                seen.add(key)
                if len(seen)>300:
                    truncated=True;break
                try:
                    images=archive_images(path)
                    if not any(supported_ref(ref) for ref in images): continue
                    found.append(dict(path=str(path), images=images, size=path.stat().st_size))
                except (OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError) as error:
                    rejected.append(dict(path=str(path), error=str(error)))
            if truncated: break
        if truncated: break
    log(f'磁盘检查完成：{len(found)} 个应用 / 依赖归档，正在读取本机 Docker 镜像')
    docker=docker_inventory()
    log(docker['message'])
    return dict(archives=found, rejected=rejected, truncated=truncated, docker=docker,
                roots=[str(p) for p in roots])

def export_docker(cache, ref, expected_id, log):
    if not supported_ref(ref): raise ValueError('请选择已识别的 OJ 应用或 MySQL / Nginx / phpMyAdmin 依赖镜像')
    if not isinstance(expected_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_id):
        raise ValueError('镜像标识无效，请重新识别')
    image=json.loads(docker_run(['image','inspect','--platform','linux/amd64',ref]))[0]
    if image.get('Id')!=expected_id: raise ValueError('镜像标签内容已变化，请重新识别后导出')
    if image.get('Os')!='linux' or image.get('Architecture')!='amd64': raise ValueError('仅支持 Linux amd64 镜像')
    from artifacts import file_hash
    catalog=cache.describe()
    for current in catalog['archives']:
        if current['id']!=catalog['selected'].get(ref) or expected_id not in current.get('identities',{}).get(ref,[]):
            continue
        try:
            if file_hash(current['path'])==current['sha256']:
                log('复用已校验的本地镜像归档：'+current['path'])
                return current
        except OSError:
            pass
    if shutil.disk_usage(cache.root).free < image.get('Size',0)*2+128*1024**2:
        raise ValueError('本地磁盘空间不足，导出与导入需要预留两份镜像空间')
    # Verify the exported identity before registering it; a concurrent retag
    # must not silently substitute another image.
    with tempfile.TemporaryDirectory(prefix='docker-export-',dir=cache.root) as folder:
        archive=Path(folder)/'docker-save.tar'
        log('正在导出本机镜像：'+ref+'；此操作不访问镜像仓库')
        docker_run(['image','save','--platform','linux/amd64','--output',str(archive),ref], timeout=1800)
        from artifacts import archive_images
        identities={}
        archive_images(archive,identities)
        if expected_id not in identities.get(ref,[]):
            raise ValueError('导出归档的镜像标识发生变化，未登记缓存，请重新识别')
        result=cache.import_file(archive,log)
    log('已导出并导入本地缓存：'+result['path'])
    return result
