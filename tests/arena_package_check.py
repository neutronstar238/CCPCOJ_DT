"""Frozen EXE API/cache check with an inert ELF fixture; never executes it."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_arena import sample


def main():
    exe=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        proc=subprocess.Popen([str(exe),'--no-browser','--data-dir',str(root/'state')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=0x08000000)
        try:
            address=''
            for _ in range(10):
                line=proc.stdout.readline().decode(errors='replace').strip()
                if line.startswith('http://'):address=line;break
            assert address,'EXE did not start'
            base,token=address.split('/#')
            def api(path,data=None):
                req=urllib.request.Request(base+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-CSG-Token':token})
                with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
            with urllib.request.urlopen(base+'/arena.js') as r:assert b'arenaMount' in r.read()
            with urllib.request.urlopen(base+'/arena-scan.js') as r:assert b'arenaScanMount' in r.read()
            assert api('arena/scan/state')['status']=='尚未扫描'
            assert api('arena/config')['config']['servers']==[]
            c=sample();artifact=root/'inert-fixture';artifact.write_bytes(b'\x7fELFnever-execute-this-fixture')
            c['artifacts']['server-linux'].update(path=str(artifact),sha256='')
            windows=root/'inert-fixture.exe';windows.write_bytes(b'MZnever-execute-this-fixture')
            c['artifacts']['server-windows'].update(path=str(windows),sha256='')
            api('arena/save',{'config':c})
            assert len(api('arena/preview',{'role':'client'})['targets'])==3
            assert b'ssh-secret' not in (root/'state/arena.dpapi').read_bytes()
            api('arena/run',{'role':'server','action':'cache'})
            for _ in range(100):
                state=api('arena/state')
                if not state['running']:break
                time.sleep(.1)
            assert state['status']=='完成',state
            saved=api('arena/config')['config']
            assert saved['artifacts']['server-linux']['sha256']==hashlib.sha256(artifact.read_bytes()).hexdigest()
            artifact.unlink()
            windows.unlink()
            api('arena/run',{'role':'server','action':'cache'})
            for _ in range(100):
                state=api('arena/state')
                if not state['running']:break
                time.sleep(.1)
            assert state['status']=='完成',state
            print('Frozen EXE passed: static page, APIs, DPAPI vault, range preview, local cache hash persistence and reuse without source. No target connection.')
        finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close()


if __name__=='__main__':main()
