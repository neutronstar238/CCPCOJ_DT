"""Launch the frozen executable and exercise its SSH worker against the local fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ssh_check import Fixture
from test_console import config,web

def main():
    root=Path(__file__).resolve().parents[1]
    exe=Path(sys.argv[1]) if len(sys.argv)>1 else root/'dist/CSGOJ-Deployment-Console-Windows/app/CSGOJDeploy.exe'
    with tempfile.TemporaryDirectory() as temp:
        fixture=Fixture(Path(temp)/'ssh')
        proc=subprocess.Popen([str(exe),'--no-browser','--data-dir',str(Path(temp)/'state')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=0x08000000)
        try:
            address=''
            for _ in range(8):
                line=proc.stdout.readline().decode(errors='replace').strip()
                if line.startswith('http://'):address=line;break
                if proc.poll() is not None:raise RuntimeError(line)
            assert address,'frozen server did not start'
            base,token=address.split('/#');headers={'X-CSG-Token':token,'Content-Type':'application/json'}
            for path,expected in [('/',b'CSGOJ'),('/app.js',b'use strict'),('/ssh.js',b'use strict'),('/style.css',b':root')]:
                with urllib.request.urlopen(base+path,timeout=10) as r:assert expected in r.read()
            def api(path,obj=None,raw=None):
                body=raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
                req=urllib.request.Request(base+'/api/'+path,data=body,headers=headers)
                with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
            api('ssh/save',{'profiles':[dict(keyName='pcj-test',host='127.0.0.1',user='test',sshPort=fixture.port,password='fixture-pass')]})
            api('ssh/run',{'action':'authorize','names':['pcj-test']})
            for _ in range(120):
                ssh_state=api('ssh/state')
                if not ssh_state['running']:break
                time.sleep(.1)
            assert ssh_state['results']['pcj-test']['ok'],ssh_state
            assert api('config')['config']['nodes']==[]
            c=config([web(role='standby')]);n=c['nodes'][0];n.update(host='127.0.0.1',sshPort=fixture.port,user='test',password='fixture-pass',elevatePassword='fixture-sudo',path='/home/test/cpc')
            tool=bytearray(100);tool[:6]=b'\x7fELF\x02\x01';tool[18:20]=b'\x3e\x00'
            c['tool']=api('tool',raw=tool)
            c=api('save',{'config':c})['config']
            api('run',{'config':c,'action':'deploy'})
            for _ in range(120):
                state=api('state')
                if not state['running']:break
                time.sleep(.1)
            assert state['status']=='完成',state['logs']
            assert api('config')['config']['nodes'][0]['prepared'] is True
            assert (Path(temp)/'state/keys/web01').exists()
            assert all(j['action']=='prepare' for j in fixture.jobs)
            for line in state['logs']:assert 'fixture-pass' not in line and 'fixture-sudo' not in line
            print('PASS: frozen Windows EXE starts without Python, renders static assets, DPAPI save, generated SSH key, SFTP remote worker upload, standby job and status persistence.')
        finally:
            proc.terminate();proc.wait(timeout=10);fixture.close()

if __name__=='__main__':main()
