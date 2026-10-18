"""Build a self-contained Windows ZIP from an explicit allowlist; never include state."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
def main():
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','CSGOJDeploy',
                    '--add-data','web;web','--add-data','templates/CSGOJ节点导入模板.xlsx;templates','--add-data','remote_ops.py;.','--add-data','database_storage.py;.', 'server.py'],cwd=ROOT,check=True)
    stage=ROOT/'dist'/'release-v1.10.3'/'CSGOJ-Deployment-Console-Windows'
    if stage.exists(): raise RuntimeError('发布目录已存在；请保留旧产物并将其改名后重新打包')
    stage.mkdir(parents=True);shutil.copytree(ROOT/'dist/CSGOJDeploy',stage/'app')
    docs_root = ROOT if (ROOT/'docs').exists() else ROOT.parent
    for f in ['README.md','启动部署工作台.cmd']:shutil.copy2(docs_root/f,stage/f)
    shutil.copytree(docs_root/'docs',stage/'docs')
    (stage/'verification').mkdir()
    for proof in (docs_root/'verification').iterdir():
        if proof.suffix=='.md':shutil.copy2(proof,stage/'verification'/proof.name)
    shutil.copytree(ROOT/'templates',stage/'templates',ignore=shutil.ignore_patterns('*.ndjson'))
    source=stage/'source';source.mkdir()
    for f in ['release_audit.py','image_discovery.py','package.json','local_deploy.py','local_image_cache.py','registry_download.py','database_storage.py','artifacts.py','credential_export.py','ssh_alias.py','models.py','storage.py','transport.py','remote_ops.py','engine.py','oj.py','server.py','xlsx_import.py','ssh_manager.py','requirements.txt','requirements-dev.txt','build.py']:
        shutil.copy2(ROOT/f,source/f)
    shutil.copytree(ROOT/'web',source/'web');shutil.copytree(ROOT/'tests',source/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(ROOT/'templates',source/'templates',ignore=shutil.ignore_patterns('*.ndjson'))
    licenses=stage/'THIRD_PARTY_LICENSES';licenses.mkdir()
    dependencies=[]
    for name in ['paramiko','cryptography','bcrypt','cffi','PyNaCl','invoke','pycparser','pyinstaller']:
        dist=importlib.metadata.distribution(name);dependencies.append(dict(name=name,version=dist.version))
        for file in dist.files or []:
            if any(part.lower() in ('licenses','license','license.txt','copying','notice') or part.lower().startswith('license.') for part in file.parts):
                path=Path(dist.locate_file(file))
                if path.is_file():
                    dest=licenses/name/str(file).replace('..','_');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    (stage/'dependencies.json').write_text(json.dumps(dependencies,indent=2),encoding='utf-8')
    (stage/'THIRD_PARTY_NOTICE.txt').write_text('Runtime dependencies and license notices are included in THIRD_PARTY_LICENSES and app/_internal.\n'
        'The official Linux csgoj-deploy program and OJ Docker images are not redistributed in this ZIP. Select or download them in the UI.\n'
        'Sources: https://github.com/paramiko/paramiko ; https://github.com/pyca/cryptography ; https://pyinstaller.org ; https://github.com/SZTU-ACM/CCPCOJ-2\n',encoding='utf-8')
    manifest=[]
    for path in sorted(stage.rglob('*')):
        if path.is_file():
            rel=path.relative_to(stage).as_posix()
            if any(part in ('keys','backups','.venv') for part in path.relative_to(stage).parts) or path.suffix=='.dpapi': raise RuntimeError('禁止打包运行资料：'+rel)
            manifest.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+rel)
    (stage/'SHA256SUMS.txt').write_text('\n'.join(manifest)+'\n',encoding='utf-8')
    dest=ROOT/'dist'/'releases'/'CSGOJ-Deployment-Console-Windows-v1.10.3.zip';dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(stage.rglob('*')):
            if path.is_file():z.write(path,stage.name+'/'+path.relative_to(stage).as_posix())
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix('.zip.sha256').write_text(digest+'  '+dest.name+'\n',encoding='ascii')
    from release_audit import audit
    report=audit(dest)
    dest.with_suffix('.zip.privacy.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if report['findings']:raise RuntimeError('发布包脱敏检查未通过；查看 privacy.json 中的文件名与问题类别，不打印匹配内容')
    print(json.dumps(dict(zip=str(dest),bytes=dest.stat().st_size,sha256=digest),ensure_ascii=False))

if __name__=='__main__':main()
