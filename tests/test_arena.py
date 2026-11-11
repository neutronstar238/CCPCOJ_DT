import copy
import hashlib
import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.request
import urllib.error

from arena_deploy import ArenaDeployment, normalize, defaults, targets, preview, bundle, cache_artifact, CONTROLLER
from arena_payloads import LINUX_SCRIPT, WINDOWS_SCRIPT


def sample():
    c=defaults()
    c['servers']=[dict(id='s1',name='主控',host='192.0.2.10',os='linux',user='operator',password='ssh-secret',rootPassword='root-secret')]
    c['clients']=[dict(id='r1',name='教室',prefix=[192,0,2],start=20,end=22,os='linux',user='student',password='client-secret',server='s1')]
    return normalize(c)


class ArenaConfigTests(unittest.TestCase):
    def test_selected_targets_do_not_rewrite_saved_ranges(self):
        c=sample(); original=copy.deepcopy(c)
        chosen=['192.0.2.20','192.0.2.22']
        self.assertEqual([n['host'] for n in targets(c,'client',chosen)],chosen)
        b=bundle(c,'client','status',{},chosen)
        self.assertEqual(set(b['inventory']['all']['hosts']),{'node_192_0_2_20','node_192_0_2_22'})
        self.assertEqual(len(preview(c,'client',chosen)),2)
        self.assertEqual(c,original)
        self.assertEqual(len(targets(c,'client',[])),3)
        for selection in (['192.0.2.99'],'192.0.2.20',''):
            with self.assertRaises(ValueError):targets(c,'client',selection)

    def test_range_expansion_and_binding(self):
        c=sample();c['servers'].append(dict(c['servers'][0],id='s2',host='198.51.100.5',servicePort=40000))
        c['clients'].append(dict(c['clients'][0],id='r2',prefix=[198,51,100],start=200,end=201,server='s2',os='windows',port=''))
        nodes=targets(normalize(c),'client')
        self.assertEqual([n['host'] for n in nodes],['192.0.2.20','192.0.2.21','192.0.2.22','198.51.100.200','198.51.100.201'])
        self.assertEqual(nodes[-1]['port'],5986)
        self.assertEqual(nodes[-1]['endpoint'],dict(host='198.51.100.5',port=40000,scheme='https'))
        self.assertEqual(len(preview(c,'server')),2)
        self.assertNotIn('secret',json.dumps(preview(c,'client')))

    def test_invalid_ranges_and_overlaps(self):
        for changes in (dict(start=24,end=20),dict(start=0),dict(end=255),dict(prefix=[192,0,256]),dict(port=0),dict(server='missing')):
            c=sample();c['clients'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):normalize(c)
        c=sample();c['clients'].append(dict(c['clients'][0],id='r2',start=22,end=30))
        with self.assertRaisesRegex(ValueError,'重叠'):normalize(c)
        c=sample();c['clients'][0].update(start=10,end=20)
        with self.assertRaisesRegex(ValueError,'重叠'):normalize(c)

    def test_endpoint_validation_and_download_credentials(self):
        for changes in (dict(port=65536),dict(servicePort=0),dict(host='127.0.0.1'),dict(host='x; touch file'),dict(scheme='ftp')):
            c=sample();c['servers'][0].update(changes)
            with self.assertRaises(ValueError):normalize(c)
        for url in ('http://example.org/x','https://user:password@example.org/x'):
            c=sample();c['artifacts']['server-linux']['url']=url
            with self.assertRaises(ValueError):normalize(c)


class ArenaCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'server';self.source.write_bytes(b'\x7fELFtest-artifact')
        self.sha=hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_local_cache_integrity_and_reuse_without_source(self):
        spec=dict(path=str(self.source),url='',sha256=self.sha)
        p=cache_artifact(self.root/'cache','server-linux',spec)
        self.source.unlink()
        self.assertEqual(cache_artifact(self.root/'cache','server-linux',spec),p)
        p.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):cache_artifact(self.root/'cache','server-linux',spec)

    def test_checksum_and_archive_rejected_without_partial_files(self):
        spec=dict(path=str(self.source),url='',sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'SHA256'):cache_artifact(self.root/'cache','server-linux',spec)
        spec['sha256']='';self.source.write_bytes(b'PKzip')
        with self.assertRaisesRegex(ValueError,'单文件'):cache_artifact(self.root/'cache','server-linux',spec)
        self.assertFalse(list((self.root/'cache').glob('*.part')))

    def test_download_requires_digest_before_network(self):
        with patch('arena_deploy.urllib.request.urlopen') as fetch:
            with self.assertRaisesRegex(ValueError,'SHA256'):cache_artifact(self.root/'cache','server-linux',dict(path='',url='https://example.org/x',sha256=''))
            fetch.assert_not_called()


class ArenaOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.manager=ArenaDeployment(Mock(root=self.root))

    def test_config_vault_and_secret_redaction(self):
        with patch('arena_deploy.dpapi',side_effect=lambda data,*args:b'vault:'+data if not args else data[6:]),patch('arena_deploy.restrict'):
            c=self.manager.save(sample())
            self.assertTrue(self.manager.path.read_bytes().startswith(b'vault:'))
            self.assertEqual(self.manager.config(),c)
            self.manager.log('ssh-secret root-secret client-secret')
            self.assertNotIn('secret',str(self.manager.status()))
        self.assertFalse((self.root/'config.dpapi').exists())

    def test_artifacts_all_ready_before_any_remote_execution(self):
        c=sample();c['clients'].append(dict(c['clients'][0],id='win',prefix=[198,51,100],os='windows'))
        with patch.object(self.manager,'config',return_value=c),patch('arena_deploy.cache_artifact',side_effect=ValueError('missing artifact')),patch('arena_deploy.subprocess.Popen') as execute:
            self.manager._run(c,'client','deploy')
            execute.assert_not_called();self.assertEqual(self.manager.status()['status'],'失败')

    def test_inventory_credentials_are_separate_from_tasks_and_preview(self):
        c=sample();c['clients'].append(dict(c['clients'][0],id='r2',prefix=[198,51,100],os='windows',port=5986))
        b=bundle(c,'client','status',{})
        self.assertEqual(len(b['inventory']['all']['hosts']),6)
        self.assertEqual(len(b['playbook']),2)
        self.assertNotIn('client-secret',json.dumps(b['playbook']))
        win=b['inventory']['all']['hosts']['node_198_51_100_20']
        self.assertEqual(win['ansible_winrm_server_cert_validation'],'validate')
        linux=b['inventory']['all']['hosts']['node_192_0_2_20']
        self.assertTrue(linux['ansible_become']);self.assertEqual(linux['ansible_become_method'],'sudo')
        self.assertEqual(linux['ansible_become_password'],'client-secret')

    def test_status_needs_no_artifact_or_install_tasks(self):
        b=bundle(sample(),'server','status',{})
        tasks=b['playbook'][0]['tasks']
        self.assertFalse(any('ansible.builtin.copy' in t for t in tasks))
        self.assertFalse(tasks[0]['changed_when'])
        compile(LINUX_SCRIPT,'linux-installer','exec');compile(CONTROLLER,'controller','exec')

    def test_remote_tasks_have_finite_timeouts(self):
        p=self.root/'artifact';p.write_bytes(b'ELF')
        for action in ('deploy','distribute','status'):
            with patch('arena_deploy.wsl_path',return_value='/mnt/e/cache/artifact'):
                b=bundle(sample(),'server',action,{'server-linux':p})
            for task in b['playbook'][0]['tasks']:
                self.assertGreater(task['timeout'],0)
                self.assertLessEqual(task['timeout'],300)

    def test_deploy_uses_only_verified_cache_sources(self):
        p=self.root/'artifact';p.write_bytes(b'ELF')
        with patch('arena_deploy.wsl_path',return_value='/mnt/e/cache/artifact'):
            b=bundle(sample(),'client','deploy',{'client-linux':p})
        tasks=b['playbook'][0]['tasks']
        self.assertEqual(tasks[1]['ansible.builtin.copy']['src'],'/mnt/e/cache/artifact')
        self.assertNotIn('get_url',json.dumps(b))

    def test_distribute_stages_selected_binary_without_install_or_enrollment(self):
        p=self.root/'selected-artifact';p.write_bytes(b'\x7fELFselected-version')
        c=sample();c['releaseTag']='v1.99.0'
        with patch('arena_deploy.wsl_path',return_value='/mnt/e/cache/selected-artifact'):
            b=bundle(c,'client','distribute',{'client-linux':p})
        tasks=b['playbook'][0]['tasks']
        self.assertEqual(tasks[1]['ansible.builtin.copy']['src'],'/mnt/e/cache/selected-artifact')
        self.assertEqual(tasks[2]['ansible.builtin.debug']['msg']['version'],'v1.99.0')
        self.assertNotIn('ansible.builtin.command',json.dumps(tasks));self.assertNotIn('api/enroll',json.dumps(tasks))

    def test_cache_can_start_before_targets_and_downloads_both_platforms(self):
        c=normalize(defaults())
        with patch.object(self.manager,'config',return_value=c),patch('arena_deploy.threading.Thread') as thread:
            self.manager.start('client','cache');thread.return_value.start.assert_called_once()
        self.manager.running=False
        paths=[]
        for platform in ('linux','windows'):
            p=self.root/platform;p.write_bytes(b'fixture');paths.append(p)
        with patch.object(self.manager,'config',return_value=c),patch.object(self.manager,'save'),patch('arena_deploy.cache_artifact',side_effect=paths) as cache,patch('arena_deploy.subprocess.Popen') as remote:
            self.manager._run(c,'client','cache')
        self.assertEqual([a.args[1] for a in cache.call_args_list],['client-linux','client-windows']);remote.assert_not_called()


class ArenaAPITests(unittest.TestCase):
    def test_authenticated_routes_and_busy_gate(self):
        from storage import Store
        from server import make_server
        with tempfile.TemporaryDirectory() as tmp:
            srv=make_server(Store(tmp));thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(srv.server_port)
            def request(path,data=None,token=True):
                req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-CSG-Token':srv.token if token else ''})
                with urllib.request.urlopen(req) as r:return json.load(r)
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:request('/api/arena/config',token=False)
                self.assertEqual(denied.exception.code,403)
                request('/api/arena/save',{'config':sample()})
                self.assertEqual(len(request('/api/arena/preview',{'role':'client'})['targets']),3)
                srv.arena.running=True
                self.assertTrue(request('/api/state')['activity']['arena'])
                with self.assertRaises(urllib.error.HTTPError) as busy:request('/api/arena/save',{'config':sample()})
                self.assertEqual(busy.exception.code,400)
            finally:srv.shutdown();srv.server_close();thread.join()


class ArenaEnrollmentTests(unittest.TestCase):
    def test_both_enrolled_and_installed_required_and_setup_always_stopped(self):
        # Execute the remote installer with a fake wizard and fake systemd; never run CSGArena.
        for result in ({'enrolled':True,'installed':True},{'enrolled':True,'installed':False},{'enrolled':False,'installed':True}):
            with self.subTest(result=result),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp)/'deploy';base.mkdir()
                script=LINUX_SCRIPT.replace('/opt/csgarena-deploy',base.as_posix()).replace('/etc/csgarena/',Path(tmp).as_posix()+'/identity/').replace('/var/lib/csgarena/',Path(tmp).as_posix()+'/state/').replace('/usr/local/lib/csgarena/',Path(tmp).as_posix()+'/lib/')
                process=Mock();process.poll.return_value=None
                def launch(*args,**kwargs):
                    self.assertEqual(args[0],['fixture-binary','setup'])
                    kwargs['stdout'].write(b'http://127.0.0.1:12345/\n');kwargs['stdout'].flush();return process
                responses=[io.BytesIO(json.dumps({'hit':{'host':'192.0.2.10','port':39721,'scheme':'https'}}).encode()),io.BytesIO(json.dumps(result).encode())]
                with patch('os.geteuid',return_value=0,create=True),patch('subprocess.Popen',side_effect=launch),patch('subprocess.run',return_value=Mock(returncode=0)),patch('urllib.request.urlopen',side_effect=responses) as post,patch('sys.argv',['installer',json.dumps({'role':'client','action':'deploy','endpoint':{'host':'192.0.2.10','port':39721,'scheme':'https'}}),'fixture-binary']),patch('sys.stdout',new=io.StringIO()):
                    if all(result.values()):exec(compile(script,'installer','exec'),{})
                    else:
                        with self.assertRaises(SystemExit):exec(compile(script,'installer','exec'),{})
                    self.assertEqual(post.call_count,2)
                    request=json.loads(post.call_args_list[1].args[0].data)
                    self.assertEqual(request['hit']['host'],'192.0.2.10')
                    self.assertEqual(json.loads(post.call_args_list[0].args[0].data)['address'],'https://192.0.2.10')
                    self.assertNotIn('client_id',request);self.assertNotIn('token',request)
                process.terminate.assert_called_once()
                self.assertEqual((base/'client-enrollment.json').exists(),all(result.values()))


if __name__=='__main__':unittest.main()
