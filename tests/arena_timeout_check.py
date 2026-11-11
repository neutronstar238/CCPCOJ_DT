"""Real WSL Ansible regression: one stalled host must not block a healthy host."""
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arena_deploy import CONTROLLER

hosts={name:dict(ansible_connection='local',ansible_python_interpreter='/usr/bin/python3') for name in ('blocked','healthy')}
data=dict(inventory={'all':{'hosts':hosts}},playbook=[dict(hosts='all',gather_facts=False,tasks=[
    dict(name='Bound a stalled channel equivalent',timeout=1,changed_when=False,
         **{'ansible.builtin.command':{'argv':['python3','-c',"import time; time.sleep(4 if '{{ inventory_hostname }}' == 'blocked' else 0)"]}}),
    dict(name='Healthy host continues',**{'ansible.builtin.debug':{'msg':'continued'}})
])])
r=subprocess.run(['wsl.exe','-d','Ubuntu-24.04','--','python3','-c',CONTROLLER],input=json.dumps(data),text=True,capture_output=True,timeout=60)
assert r.returncode==2, r.stdout+r.stderr
assert re.search(r'blocked\s+:.*failed=1',r.stdout),r.stdout
assert re.search(r'healthy\s+: ok=2.*failed=0',r.stdout),r.stdout
print('Real Ansible timeout passed: stalled host failed, healthy host completed both tasks.')
