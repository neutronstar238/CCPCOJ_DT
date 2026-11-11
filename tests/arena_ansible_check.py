"""WSL syntax checks and harmless localhost controller smoke; no venue hosts touched."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arena_deploy import CONTROLLER, bundle
from arena_payloads import WINDOWS_SCRIPT
from test_arena import sample

CHECK = r'''
import json,os,pathlib,subprocess,sys,tempfile
data=json.load(sys.stdin)
exe=os.path.expanduser('~/.local/share/csgoj-ansible/venv/bin/ansible-playbook')
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='arena-syntax-') as d:
    for i,b in enumerate(data):
        inv=pathlib.Path(d,'inventory.json'); play=pathlib.Path(d,'play.json')
        inv.write_text(json.dumps(b['inventory']));play.write_text(json.dumps(b['playbook']))
        subprocess.run([exe,'-i',str(inv),str(play),'--syntax-check'],check=True)
'''


def main():
    c=sample()
    c['servers'].append(dict(c['servers'][0],id='winserver',host='198.51.100.10',os='windows',port=5986))
    c['clients'].append(dict(c['clients'][0],id='winclients',prefix=[198,51,100],server='winserver',os='windows',port=5986))
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'artifact';p.write_bytes(b'fixture-only')
        artifacts={role+'-'+os:p for role in ('server','client') for os in ('linux','windows')}
        bundles=[bundle(c,role,action,artifacts) for role in ('server','client') for action in ('deploy','distribute','status')]
        subprocess.run(['wsl.exe','-d','Ubuntu-24.04','--','python3','-c',CHECK],input=json.dumps(bundles),text=True,check=True)
    # Exercise real stdin handoff, private inventory, module execution, output and cleanup.
    smoke={'inventory':{'all':{'hosts':{'localhost':{'ansible_connection':'local','ansible_python_interpreter':'/usr/bin/python3'}}}},
           'playbook':[{'hosts':'localhost','gather_facts':False,'tasks':[{'name':'Check private inventory permissions','ansible.builtin.command':{'argv':['python3','-c','import os,sys; assert os.stat(sys.argv[1]).st_mode & 0o077 == 0; print("private inventory OK")','{{ inventory_file }}']},'changed_when':False}]}]}
    subprocess.run(['wsl.exe','-d','Ubuntu-24.04','--','python3','-c',CONTROLLER],input=json.dumps(smoke),text=True,check=True)
    parser='$errors=$null; [void][System.Management.Automation.Language.Parser]::ParseInput([Console]::In.ReadToEnd(),[ref]$null,[ref]$errors); if($errors.Count){$errors | Out-String | Write-Output; exit 1}; Write-Output "Windows installer PowerShell syntax OK"'
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',parser],input=WINDOWS_SCRIPT,text=True,check=True)
    print('All six role/action playbooks (both platforms), WSL controller smoke and PowerShell parser passed.')


if __name__=='__main__':main()
