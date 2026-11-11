"""CSGArena's versioned GitHub release naming and optional gh authentication."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit, quote

REPOSITORY = 'SZTU-ACM/CSGArena'
DEFAULT_TAG = 'v1.16.2'
KINDS = ('server-linux', 'server-windows', 'client-linux', 'client-windows')
# Verified against GitHub release asset metadata, not locally computed trust anchors.
DEFAULT_SHA256 = {
    'server-linux': '4dbd5cd97c46a8d89490ca0bb1ce7c7601462b84440a2c54b5751a2684d7e3ef',
    'server-windows': '8082ba485c104214b17ffe83b602edf3f66e2ebb1467687d23e5c5b20923397e',
    'client-linux': '9caec3106d26ea9f5e9a5507cb96c7b0c502e270f702478debad79473ef030de',
    'client-windows': 'd91033b0a29c027ad35f1057178c237715a8baff7c19cb1688e4bc660975f3e3',
}


def release_tag(value):
    value = str(value).strip()
    prefix = 'https://github.com/' + REPOSITORY + '/releases/tag/'
    if value.startswith(prefix): value = value[len(prefix):].rstrip('/')
    if not re.fullmatch(r'v?\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?', value):
        raise ValueError('填写版本号（如 v1.16.2）或 SZTU-ACM/CSGArena 的 Release 链接')
    return value if value.startswith('v') else 'v' + value


def asset_name(kind, tag):
    if kind not in KINDS: raise ValueError('未知管控产物类型')
    role, platform = kind.split('-')
    return f'csgarena_{role}_{platform}_amd64_{release_tag(tag)}' + ('.exe' if platform == 'windows' else '')


def asset_url(kind, tag):
    return f'https://github.com/{REPOSITORY}/releases/download/{release_tag(tag)}/{asset_name(kind, tag)}'


def default_artifacts():
    return {k: dict(path='', url=asset_url(k, DEFAULT_TAG), sha256=DEFAULT_SHA256[k]) for k in KINDS}


def gh_command(args):
    exe = shutil.which('gh')
    if not exe: raise ValueError('此 Release 需要 GitHub 登录：请安装 GitHub CLI 并执行 gh auth login，或使用本地安装包')
    r = subprocess.run([exe] + args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=600, creationflags=0x08000000 if os.name == 'nt' else 0)
    if r.returncode: raise ValueError('GitHub CLI 读取失败：请检查 gh auth status、仓库访问权限和网络；未保存登录令牌')
    return r.stdout


def release_error(exc):
    """Report a useful location, without paths, tokens or command arguments."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f'{Path(frames[-1].filename).name}:{frames[-1].lineno}' if frames else 'release'
    return f'发布查询失败：{type(exc).__name__}（{where}），请检查网络、GitHub CLI 和本地运行环境'


def fetch_release(value):
    tag = release_tag(value)
    endpoint = f'repos/{REPOSITORY}/releases/tags/{tag}'
    req = urllib.request.Request('https://api.github.com/' + endpoint, headers={'User-Agent': 'CSGOJ-Deployment-Console', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: data = json.loads(r.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as e:
        if e.code not in (403, 404): raise ValueError(f'GitHub Release 查询失败（HTTP {e.code}）') from None
        data = json.loads(gh_command(['api', endpoint]))
    if data.get('tag_name') != tag or data.get('draft'): raise ValueError('Release 标签不匹配或尚未发布')
    artifacts = {kind: release_asset(data, kind, tag) for kind in KINDS}
    return dict(tag=tag, url=f'https://github.com/{REPOSITORY}/releases/tag/{tag}', artifacts=artifacts)


def release_asset(data, kind, tag):
    name = asset_name(kind, tag)
    matches = [a for a in data.get('assets', []) if a.get('name') == name]
    if len(matches) != 1: raise ValueError('缺少唯一的安装包：' + name)
    a = matches[0]; checksum = a.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', checksum): raise ValueError(name + ' 缺少 GitHub SHA256')
    if a.get('browser_download_url') != asset_url(kind, tag): raise ValueError(name + ' 下载地址不符合规范')
    return dict(path='', url=a['browser_download_url'], sha256=checksum[7:].lower())


def release_summary(data):
    tag = str(data.get('tag_name') or '')
    checks = []
    for kind in KINDS:
        error = ''
        try:
            if release_tag(tag) != tag: raise ValueError('标签必须使用 v 版本号格式')
            release_asset(data, kind, tag)
        except ValueError as e: error = str(e)
        checks.append(dict(kind=kind, available=not error, error=error))
    draft = bool(data.get('draft'))
    return dict(tag=tag, name=str(data.get('name') or tag), publishedAt=data.get('published_at') or '',
                url=f'https://github.com/{REPOSITORY}/releases/tag/{quote(tag, safe="")}',
                prerelease=bool(data.get('prerelease')), draft=draft, assets=checks,
                available=not draft and all(a['available'] for a in checks),
                reason='草稿尚未发布' if draft else '；'.join(a['error'] for a in checks if a['error']))


def list_releases():
    """Read every visible API page; never pretend a truncated result is complete."""
    releases, seen = [], set()
    page, authenticated = 1, False
    while True:
        endpoint = f'repos/{REPOSITORY}/releases?per_page=100&page={page}'
        if authenticated:
            data = json.loads(gh_command(['api', endpoint]))
        else:
            req = urllib.request.Request('https://api.github.com/' + endpoint, headers={'User-Agent': 'CSGOJ-Deployment-Console', 'Accept': 'application/vnd.github+json'})
            try:
                with urllib.request.urlopen(req, timeout=30) as r: data = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code not in (403, 404): raise ValueError(f'Release 列表查询失败（HTTP {e.code}），未返回不完整列表') from None
                authenticated = True
                data = json.loads(gh_command(['api', endpoint]))
        if not isinstance(data, list): raise ValueError('GitHub Release 列表格式无效')
        added = 0
        for item in data:
            key = item.get('id') or item.get('tag_name')
            if key in seen: continue
            seen.add(key); added += 1; releases.append(release_summary(item))
        if len(data) < 100: break
        if not added: raise ValueError('分页列表重复，请重新扫描 Releases')
        page += 1
    return dict(repository=REPOSITORY, releases=releases, total=len(releases))


def authenticated_download(url, destination):
    """Only delegate exact assets of this repository; credentials stay in gh's keyring."""
    parsed = urlsplit(url)
    prefix = f'/{REPOSITORY}/releases/download/'
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or not parsed.path.startswith(prefix):
        raise ValueError('下载被拒绝；自动 GitHub 登录仅用于 SZTU-ACM/CSGArena 的标准 Release 安装包')
    parts = parsed.path[len(prefix):].split('/')
    if len(parts) != 2: raise ValueError('Release 安装包路径无效')
    tag, name = parts
    if tag != release_tag(tag) or name not in [asset_name(k, tag) for k in KINDS]: raise ValueError('Release 安装包命名不符合规范')
    with tempfile.TemporaryDirectory(prefix='arena-gh-', dir=Path(destination).parent) as directory:
        gh_command(['release', 'download', tag, '--repo', REPOSITORY, '--pattern', name, '--dir', directory])
        source = Path(directory) / name
        if not source.is_file(): raise ValueError('GitHub 未下载到指定安装包')
        source.replace(destination)
