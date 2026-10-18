"""Audit release bytes without printing matched secrets. Run before publishing."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import zipfile


def audit(path):
    findings=[]; reviewed=[]; checked=0
    home=str(Path.home()).replace('\\','/').lower().encode()
    patterns={
        'private-key':rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{40,}',
        'github-token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
        'aws-access-key':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
        'slack-token':rb'\bxox[baprs]-[0-9A-Za-z-]{25,}\b',
        'credential-url':rb'https?://[^\s/@:]{2,}:[^\s/@]{4,}@',
    }
    def scan(name,raw):
        nonlocal checked
        checked+=1
        for category,pattern in patterns.items():
            matches=re.findall(pattern,raw)
            if not matches:continue
            if category=='credential-url' and name.endswith('/tests/test_artifacts.py') and all(x==b'https://' + b'user:password@' for x in matches):
                reviewed.append(dict(file=name,category='reserved-example-url-in-rejection-test'))
            else:findings.append(dict(file=name,category=category))
        for encoding in ('utf-8','utf-16le'):
            text=raw.decode(encoding,errors='ignore').replace('\\','/').lower()
            while '//' in text:text=text.replace('//','/')
            if home.decode() in text:findings.append(dict(file=name,category='current-user-profile-path'));break
        if name.endswith(('.xlsx','.zip')):
            with zipfile.ZipFile(io.BytesIO(raw)) as nested:
                for entry in nested.infolist():
                    if not entry.is_dir():scan(name+'!'+entry.filename,nested.read(entry))
    with zipfile.ZipFile(path) as package:
        for entry in package.infolist():
            if entry.is_dir():continue
            name=entry.filename;parts=Path(name).parts
            if 'verification' in parts and Path(name).suffix.lower() != '.md':
                findings.append(dict(file=name,category='non-text-verification-artifact'))
            if any(p in ('keys','.ssh','backups','.venv','node_modules') for p in parts) or name.endswith(('.dpapi','.log','.env')) or Path(name).name in ('known_hosts','id_rsa','id_ed25519'):
                findings.append(dict(file=name,category='runtime-or-private-file'))
            scan(name,package.read(entry))
    # Verify compressed Python payloads too, not only visible ZIP source files.
    from PyInstaller.archive.readers import CArchiveReader,ZlibArchiveReader
    import tempfile
    with zipfile.ZipFile(path) as package,tempfile.TemporaryDirectory() as temp:
        for name in package.namelist():
            if not name.endswith('/CSGOJDeploy.exe'):continue
            executable=Path(temp)/'app.exe';executable.write_bytes(package.read(name))
            archive=CArchiveReader(str(executable))
            for member in archive.toc:
                raw=archive.extract(member)
                if not raw:continue
                scan(name+'!'+member,raw)
                if member.endswith('.pyz'):
                    pyz=Path(temp)/'payload.pyz';pyz.write_bytes(raw)
                    payload=ZlibArchiveReader(str(pyz))
                    for module in payload.toc:
                        data=payload.extract(module,raw=True)
                        if data:scan(name+'!'+member+'!'+module,data)
    return dict(zip=Path(path).name,sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                membersChecked=checked,findings=findings,reviewedExamples=reviewed,
                note='Pattern and path scan; test fixture credentials are non-production examples. Verification artifacts must be Markdown only; screenshots are excluded.')


if __name__=='__main__':
    import sys
    result=audit(sys.argv[1]);print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['findings']))
