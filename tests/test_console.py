import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import default_config, normalize, protect_managed, redact
from storage import Store, dpapi
from engine import Engine
from server import make_server

def web(name='web01', role='active'):
    return dict(id=name, kind='web', role=role, host='192.0.2.' + ('10' if name=='web01' else '11'), user='ccpc00', sshPort=22, keyName=name, path='/home/ccpc00/cpc', elevation='sudo', adminUser='admin', site=name, port=20080, myadminPort=20050)

def judge(role='active'):
    return dict(id='pcj01', kind='judge', role=role, host='192.0.2.20', user='ccpc00', sshPort=22, keyName='pcj01', path='/home/ccpc00/cpc', elevation='sudo', web='web01', pods=1)

def config(nodes=None):
    c=default_config();c.update(version='2.0.281-exp',toolVersion='2.0.271');c['nodes']=nodes or [web(),judge()];return normalize(c)

class ConfigTests(unittest.TestCase):
    def test_defaults_have_no_implicit_versions(self):
        c=normalize(default_config())
        self.assertEqual(c['version'], '')
        self.assertEqual(c['toolVersion'], '')
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Store(tmp))
            c=config();c['version']=''
            for action in ('deploy','upgrade','checkUpdate','distribute','promote'):
                with self.assertRaisesRegex(ValueError,'明确选择'):engine.start(action,c,['web01'])
            self.assertFalse(engine.running)
    def test_standard_web_port_80_and_invalid_port_zero(self):
        c=config();c['nodes'][0]['port']=80
        self.assertEqual(normalize(c)['nodes'][0]['port'],80)
        c['nodes'][0]['port']=0
        with self.assertRaises(ValueError):normalize(c)

    def test_generated_secrets_stable_after_save(self):
        c=config(); self.assertEqual(c,normalize(c));self.assertEqual(len(c['nodes'][0]['secrets']['sqlRoot']),40)
        self.assertNotEqual(c['nodes'][0]['secrets']['sqlRoot'],c['nodes'][0]['secrets']['sqlUser'])
    def test_secret_redaction(self):
        c=config();n=c['nodes'][0];n['password']='test-ssh-secret';text=' '.join(n['secrets'].values())+' test-ssh-secret'
        self.assertNotIn('test-ssh-secret',redact(text,c));self.assertNotIn(n['secrets']['admin'],redact(text,c))
    def test_command_and_path_inputs_rejected(self):
        for field,value in [('host','1.2.3.4; rm -rf /'),('path','/'),('path','/home/../etc'),('keyName','../../id_rsa'),('user','$(echo x)')]:
            c=config();c['nodes'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):normalize(c)
    def test_active_judge_cannot_bind_spare(self):
        with self.assertRaises(ValueError): config([web(role='standby'),judge()])
    def test_generated_accounts_unique(self):
        j=judge();j2=copy.deepcopy(j);j2.update(id='pcj02',keyName='pcj02',host='192.0.2.21')
        c=config([web(),j,j2]);self.assertEqual([n['judgeUser'] for n in c['nodes'][1:]],['j1','j2'])
    def test_ports_on_same_host_rejected(self):
        w2=web('web02');w2.update(host='192.0.2.10',path='/home/ccpc00/second')
        with self.assertRaises(ValueError): config([web(),w2])
    def test_mirror_url_does_not_allow_path_or_password(self):
        for value in ['http://host', 'https://x:a@host','https://host/path']:
            c=config();c['mirror']=value
            with self.assertRaises(ValueError):normalize(c)
    def test_managed_identity_cannot_be_repurposed(self):
        before=config();before['nodes'][0]['prepared']=True
        after=copy.deepcopy(before);after['nodes'][0]['path']='/home/ccpc00/other'
        with self.assertRaises(ValueError):protect_managed(before,after)
        after=copy.deepcopy(before);after['nodes'][0]['host']='192.0.2.100';protect_managed(before,after)

class StorageTests(unittest.TestCase):
    def test_dpapi_roundtrip(self):
        raw=b'private-test-secret';encrypted=dpapi(raw);self.assertNotIn(raw,encrypted);self.assertEqual(dpapi(encrypted,True),raw)
    def test_vault_and_key_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(temp);c=config();s.save(c);self.assertEqual(s.load(),c)
            p,pub=s.key('pcj01');before=p.read_bytes();self.assertEqual(s.key('pcj01')[1],pub);self.assertEqual(p.read_bytes(),before)
            self.assertNotIn(c['nodes'][0]['secrets']['admin'].encode(),(s.root/'config.dpapi').read_bytes())
    def test_tool_rejects_html_and_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(temp)
            for data in [b'<html>denied</html>',b'\x7fELF\x02\x01'+b'\x00'*70]:
                with self.assertRaises(ValueError):s.tool(data)
            data=bytearray(100);data[:6]=b'\x7fELF\x02\x01';data[18:20]=b'\x3e\x00';self.assertEqual(s.tool(data)['size'],100)

class FakeStore:
    def __init__(self,root):self.root=Path(root);self.saved=None
    def save(self,c):self.saved=copy.deepcopy(c)
class FakeClient:
    def __init__(self,n):self.n=n
    def verify(self,initialize=False):return {'http':True}
    def login(self):return self

class SFTP:
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def normalize(self,_):return '/home/ccpc00'
    def chmod(self,*_):pass
    def remove(self,*_):pass
class FakeRemote:
    def __init__(self,engine,node):self.engine,self.node=engine,node;self.ssh=self
    def open_sftp(self):return SFTP()
    def put(self,*_):pass
    def op(self,action,config,**kwargs):return self.engine.op(self.node,action)
    def close(self):pass

class FakeEngine(Engine):
    def __init__(self,root,c):
        super().__init__(FakeStore(root),client_factory=FakeClient);self.c=c;self.calls=[];self.fail_action=None;self.fail_target=None
    def remote(self,n,bootstrap=False):return FakeRemote(self,n)
    def op(self,n,action,extra=None,tool=None):
        self.calls.append((n['id'],action))
        if action==self.fail_action and n['id']==self.fail_target:raise RuntimeError('injected failure')
        if action=='health':return dict(containers=[dict(image='mysql:8.0.32',imageId='same')],imageIds={'mysql:8.0.32':'same'})
        return {}
    def sql(self,w,q):
        return '0' if 'result < 4' in q else '10\t3\t1\t5'
    def snapshot(self,w):self.calls.append((w['id'],'snapshot'));return dict(path='snapshot.tar.gz',sha256='abc')
    def activate(self,n,rebuild=False):
        self.calls.append((n['id'],'activate:' + n.get('web','')))
        if self.fail_action=='activate':
            self.fail_action=None;raise RuntimeError('judge rebind failed')
        n['deployed']=True
    def prepare(self,n):self.calls.append((n['id'],'prepare'))

class WorkflowTests(unittest.TestCase):
    def test_empty_deployment_selection_never_expands_to_all_nodes(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t)
            for selection in (None, []):
                with self.assertRaisesRegex(ValueError,'请选择至少一个节点'):
                    e.start('deploy',e.c,selection)
            self.assertFalse(e.running)
            self.assertEqual(e.calls,[])
    def test_deployment_dispatch_keeps_only_explicit_selection(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.c['tool']={'sha256':'test'}
            with patch('engine.threading.Thread') as thread:
                e.start('deploy',e.c,['web02'])
            self.assertEqual(thread.call_args.kwargs['args'],('deploy',['web02'],None))
    def make(self,temp):
        c=config([web(),web('web02','standby'),judge()])
        for n in c['nodes']:
            n.update(prepared=True,preparedVersion=c['version'],deployed=n['role']=='active',deployedVersion=c['version'])
        return FakeEngine(temp,c)
    def test_spare_prepare_never_activates(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.deploy_all(['web02']);self.assertEqual(e.calls,[('web02','prepare')])
    def test_migration_changes_roles_only_after_judge_rebind(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.promote(e.node('web02'),'web01')
            self.assertEqual(e.node('pcj01')['web'],'web02');self.assertEqual(e.node('web01')['role'],'standby')
            self.assertLess(e.calls.index(('web01','snapshot')),e.calls.index(('web02','restore')))
            self.assertIn(('pcj01','activate:web02'),e.calls)
            self.assertEqual(e.node('web01')['secrets'],e.node('web02')['secrets'])
    def test_failed_restore_recovers_source(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.fail_action='restore';e.fail_target='web02'
            with self.assertRaisesRegex(RuntimeError,'injected'):e.promote(e.node('web02'),'web01')
            self.assertIn(('web01','start'),e.calls);self.assertIn(('pcj01','activate:web01'),e.calls)
            self.assertEqual(e.node('web02')['role'],'standby');self.assertEqual(e.node('pcj01')['web'],'web01')
    def test_failed_rebind_recovers_judge_mapping(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.fail_action='activate'
            with self.assertRaisesRegex(RuntimeError,'rebind'):e.promote(e.node('web02'),'web01')
            self.assertEqual(e.node('pcj01')['web'],'web01');self.assertIn(('pcj01','activate:web01'),e.calls)
    def test_preflight_failure_does_not_stop_source(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t);e.fail_action='migrationCheck';e.fail_target='web02'
            with self.assertRaises(RuntimeError):e.promote(e.node('web02'),'web01')
            self.assertNotIn(('web01','stop'),e.calls)
    def test_backup_failure_resumes_source(self):
        with tempfile.TemporaryDirectory() as t:
            e=self.make(t)
            with patch.object(e,'snapshot',side_effect=RuntimeError('disk full')):
                with self.assertRaises(RuntimeError):e.backup(e.node('web01'))
            self.assertIn(('web01','start'),e.calls);self.assertIn(('pcj01','start'),e.calls)

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=make_server(Store(self.temp.name));self.thread=threading.Thread(target=self.s.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.s.server_port)
    def tearDown(self):self.s.shutdown();self.s.server_close();self.temp.cleanup()
    def request(self,path,body=None,headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,data=body,headers=headers or {}))
    def test_autosave_preferences_without_nodes_and_preserve_business_secrets(self):
        h={'X-CSG-Token':self.s.token,'Content-Type':'application/json'}
        settings=dict(disableGui=False,neverSleep=True,performance=False,version='',mirror='',uiChoices={'releaseVersion':'','imageVersion':''})
        self.request('/api/preferences',json.dumps({'settings':settings}).encode(),h).close()
        saved=self.s.store.load();self.assertEqual(saved['nodes'],[]);self.assertFalse(saved['disableGui'])
        c=config();self.s.store.save(c)
        settings['uiChoices']['imageVersion']='2.0.287-exp';settings['version']='2.0.287-exp'
        self.request('/api/preferences',json.dumps({'settings':settings}).encode(),h).close()
        saved=self.s.store.load();self.assertEqual(saved['nodes'],c['nodes']);self.assertEqual(saved['version'],'2.0.287-exp')
        self.assertIsNone(self.s.engine.status()['job'])
        settings['version']='../invalid'
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/preferences',json.dumps({'settings':settings}).encode(),h)
        self.assertEqual(self.s.store.load(),saved)
    def test_api_requires_session_and_rejects_foreign_origin(self):
        for h in [{},{'X-CSG-Token':self.s.token,'Origin':'http://evil.invalid'}]:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/config',headers=h)
            self.assertEqual(ctx.exception.code,403)
    def test_host_header_rebinding_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/',headers={'Host':'attacker.example'})
        self.assertEqual(ctx.exception.code,403)
    def test_save_and_export_excludes_ssh_password(self):
        c=config();c['nodes'][0]['password']='only-ssh-secret'
        h={'X-CSG-Token':self.s.token,'Content-Type':'application/json'}
        self.request('/api/save',json.dumps({'config':c}).encode(),h).close()
        with self.request('/api/export',headers=h) as r:raw=r.read();self.assertNotIn(b'only-ssh-secret',raw);self.assertIn(c['nodes'][0]['secrets']['sqlRoot'].encode(),raw)
        self.assertNotIn(b'only-ssh-secret',(Path(self.temp.name)/'config.dpapi').read_bytes())
    def test_credentials_txt_all_selected_missing_and_no_mutation(self):
        c=config(); c['nodes'][0]['password']='ssh-not-exported'; c['nodes'][0]['elevatePassword']='sudo-not-exported'
        c['nodes'][0]['secrets']['admin']='admin-saved'; c['nodes'][1]['judgePassword']='judge-saved'
        del c['nodes'][0]['secrets']['myadmin']
        self.s.store.save(c)
        before=(self.s.store.root/'config.dpapi').read_bytes()
        h={'X-CSG-Token':self.s.token,'Content-Type':'application/json'}
        def export(ids):
            with self.request('/api/credentials-export',json.dumps({'ids':ids}).encode(),h) as r:
                self.assertIn('attachment',r.headers['Content-Disposition'])
                return r.read().decode('utf-8-sig')
        selected=export([c['nodes'][1]['id']])
        self.assertIn('judge-saved',selected); self.assertNotIn('admin-saved',selected)
        full=export(None)
        for secret in ['admin-saved','judge-saved',c['nodes'][0]['secrets']['sqlRoot'],c['nodes'][0]['secrets']['sqlUser']]:self.assertIn(secret,full)
        self.assertIn('缺失：本机未保存',full)
        for secret in ['ssh-not-exported','sudo-not-exported']:self.assertNotIn(secret,full)
        for ids in [[],['unknown']]:
            with self.assertRaises(urllib.error.HTTPError):export(ids)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request('/api/credentials-export',b'{}')
        self.assertEqual(denied.exception.code,403)
        self.assertEqual(before,(self.s.store.root/'config.dpapi').read_bytes())

    def test_static_traversal_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/../storage.py')
        self.assertEqual(ctx.exception.code,404)

if __name__=='__main__':unittest.main(verbosity=2)
