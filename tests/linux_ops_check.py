"""Linux-side tests in temporary directories. Docker/systemctl are mocked.
Real tar archives/extraction and path validation are exercised, never host settings.
"""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import remote_ops as ops

class LinuxChecks(unittest.TestCase):
    def test_inventory_empty_host_and_residual_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);n=dict(id='pcj01',kind='judge',pods=1)
            with patch.object(ops,'docker_exists',return_value=False):
                self.assertEqual(ops.version_inventory(n,root)['mode'],'initialize')
                (root/'data').mkdir();(root/'data/state').write_text('original')
                self.assertEqual(ops.version_inventory(n,root)['mode'],'blocked')
                self.assertEqual((root/'data/state').read_text(),'original')
    def test_inventory_lists_distributed_images_and_actual_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);n=dict(id='pcj01',kind='judge',pods=1)
            prefix='csgrandeur/ccpcoj-judge2:'
            current=[dict(name='judge-pcj01',image=prefix+'old',imageId='old-id',running=True,mounts=[])]
            def command(args,**kw):
                if args[:3]==['docker','image','ls']:return prefix+'old\n'+prefix+'new'
                if args[:3]==['docker','image','inspect']:return json.dumps([dict(Id=args[-1]+'-id',Os='linux',Architecture='amd64')])
                raise AssertionError('Unexpected mutation '+str(args))
            with patch.object(ops,'docker_exists',return_value=True),patch.object(ops.subprocess,'run',return_value=type('Result',(),{'returncode':0})()),patch.object(ops,'inspect_containers',return_value=current),patch.object(ops,'run',side_effect=command):
                inv=ops.version_inventory(n,root)
                self.assertEqual(inv['mode'],'adoptUpdate');self.assertEqual(inv['currentVersions'],['old'])
                self.assertEqual(inv['availableVersions'],['new','old']);self.assertFalse((root/'.csg-console.json').exists())
    def test_adoption_checks_original_account_without_mutating_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'csgoj-deploy').write_bytes(b'fixture')
            n=dict(id='pcj01',kind='judge',role='active',pods=1,webUrl='http://192.0.2.10:20080',judgeUser='pj01',judgePassword='original')
            x=dict(Id='container1',Image='image1',Name='/judge-pcj01',State={'Running':True},Config={'Image':'csgrandeur/ccpcoj-judge2:2.0.281-exp','Env':['CSGOJ_SERVER_USERNAME=pj01','CSGOJ_SERVER_PASSWORD=original','CSGOJ_SERVER_BASE_URL=http://192.0.2.10:20080']},HostConfig={},Mounts=[])
            def command(args,**kw):
                if args[:2]==['docker','inspect']:return json.dumps([x])
                if args[:2]==['docker','ps']:return 'judge-pcj01'
                raise AssertionError('Unexpected mutation '+repr(args))
            with patch.object(ops,'check_os'),patch.object(ops,'validate_mounts'),patch.object(ops,'run',side_effect=command):
                result=ops.adoption_inspect(n,root)
                self.assertEqual(result['version'],'2.0.281-exp');self.assertFalse((root/'.csg-console.json').exists())
                with self.assertRaisesRegex(RuntimeError,'发生变化'):ops.adopt(n,{},root,{'fingerprint':'stale'})
                self.assertFalse((root/'.csg-console.json').exists())
                ops.adopt(n,{},root,{'fingerprint':result['fingerprint']})
                self.assertTrue(json.loads((root/'.csg-console.json').read_text())['adopted'])
                n['judgePassword']='different'
                with self.assertRaisesRegex(RuntimeError,'口令不匹配'):ops.adoption_inspect(n,root)
    def test_commit_allows_mount_order_change_but_keeps_service_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mounts=[{'Destination':'/b','Source':str(root/'data/b')},{'Destination':'/a','Source':str(root/'data/a')}]
            app={'Name':'/php-demo','Id':'oldapp'}
            infra={'Name':'/demonginx-server','Id':'retained','Config':{'Env':['B=2','A=1']},'Mounts':mounts}
            ops.write_json(root/'private/update-transaction.json',{'apps':[app],'containers':[app,infra],'targetId':'sha256:new'})
            current=dict(infra,Mounts=list(reversed(mounts)),Config={'Env':['A=1','B=2']})
            with patch.object(ops,'owned'),patch.object(ops,'inspect_containers',return_value=[{'name':'php-demo','imageId':'sha256:new','running':True}]),patch.object(ops,'run',return_value=json.dumps([current])):
                result=ops.update_finish({}, {'version':'2.0.282-exp'},root)
            self.assertEqual(result['phase'],'committed')
    def test_official_stateless_myadmin_ownership_requires_correct_project_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ops.write_json(root/'.csg-console.json',{'id':'web01','kind':'web'})
            n=dict(id='web01',kind='web',site='demo')
            admin={'Config':{'Image':'phpmyadmin:5.2.3','Env':['PMA_HOST=demodb']},'NetworkSettings':{'Networks':{'democsgoj_net':{}}}}
            db={'Mounts':[{'Source':str(root/'data/mysql')}],'NetworkSettings':{'Networks':{'democsgoj_net':{}}}}
            with patch.object(ops,'inspect_containers',return_value=[{'name':'demomyadmin','mounts':[]}]),patch.object(ops,'run',side_effect=lambda a:json.dumps([admin if a[-1]=='demomyadmin' else db])):
                ops.owned(n,root)
                admin['Config']['Env']=['PMA_HOST=unrelated']
                with self.assertRaisesRegex(RuntimeError,'不属于'):ops.owned(n,root)
    def test_upgrade_recreates_app_with_pinned_image_and_keeps_runtime_credentials(self):
        saved={'Id':'a'*64,'Name':'/php-demo','Image':'sha256:old','Config':{'Image':'old:tag','Hostname':'a'*12,'Env':['DB_PASSWORD=retained','OJ_NAME=demo','OJ_UPDATE_STATIC=0','DEFAULT=old'],'Cmd':['old-cmd'],'Entrypoint':['entry']},'HostConfig':{'Binds':['/srv/demo/data/www:/var/www'],'NetworkMode':'csgoj_net'},'NetworkSettings':{'Networks':{'csgoj_net':{'Aliases':['a'*12,'php-demo'],'IPAddress':'10.250.1.2'}}}}
        def output(args,**kw):
            return json.dumps([{'Config':{'Env':['DEFAULT='+('old' if args[-1]=='sha256:old' else 'new')],'Cmd':['old-cmd' if args[-1]=='sha256:old' else 'new-cmd'],'Entrypoint':['entry']}}])
        with patch.object(ops,'run',side_effect=output),patch.object(ops,'docker_api',side_effect=[{'ApiVersion':'1.52'},{'Id':'created'}]) as api:
            self.assertEqual(ops.recreate(saved,'sha256:new',True),'created')
            body=api.call_args_list[1].args[2]
            env=dict(x.split('=',1) for x in body['Env'])
            self.assertEqual(env['DB_PASSWORD'],'retained');self.assertEqual(env['DEFAULT'],'new');self.assertEqual(env['OJ_UPDATE_STATIC'],'1')
            self.assertEqual(body['Cmd'],['new-cmd']);self.assertEqual(body['HostConfig'],saved['HostConfig'])
            self.assertEqual(body['Image'],'sha256:new')
            self.assertEqual(body['NetworkingConfig']['EndpointsConfig']['csgoj_net'],{'Aliases':['php-demo']})
    def test_multi_pod_names_match_observed_official_deployer(self):
        self.assertEqual(ops.names(dict(kind='judge',id='pcj01',pods=3)),['judge-pcj01','judge-pcj01-1','judge-pcj01-2'])
    def test_resize_six_core_host_rejects_two_pods_without_stopping(self):
        n=dict(kind='judge',role='active',id='pcj01',pods=1,path='/tmp/csg-resize-test')
        def output(args,**kwargs):return '6' if '{{.NCPU}}' in args else str(16*1024**3)
        with patch.object(ops,'owned'),patch.object(ops,'run',side_effect=output) as run:
            with self.assertRaisesRegex(RuntimeError,'资源不足'):ops.main(dict(node=n,config={'version':'2.0.281-exp'},action='judgeResizeCheck',extra={'pods':2}))
            self.assertEqual(len(run.call_args_list),2)
    def test_start_only_stopped_containers_and_database_first(self):
        n = dict(kind='web', role='active', site='demo')
        before = [dict(name=name, running=name=='demomyadmin') for name in ops.names(n)]
        after = [dict(name=name, running=True) for name in ops.names(n)]
        with patch.object(ops,'owned'), patch.object(ops,'inspect_containers',side_effect=[before,after]), patch.object(ops,'run') as run:
            ops.control(n, Path('/test'), 'start')
            starts = [c.args[0] for c in run.call_args_list]
        self.assertEqual(starts[0], ['systemctl','start','docker'])
        self.assertEqual(starts[1], ['docker','start','demodb'])
        self.assertNotIn(['docker','start','demomyadmin'], starts)
        self.assertEqual(starts[-1], ['docker','start','demonginx-server'])
    def test_start_missing_container_fails_before_partial_start(self):
        n = dict(kind='judge', role='active', id='pcj01', pods=1)
        with patch.object(ops,'owned'), patch.object(ops,'inspect_containers',return_value=[]), patch.object(ops,'run') as run:
            with self.assertRaisesRegex(RuntimeError, '容器缺失'): ops.control(n, Path('/test'), 'start')
            run.assert_called_once_with(['systemctl','start','docker'])
    def test_start_standby_rejected_before_daemon_start(self):
        with patch.object(ops,'owned'), patch.object(ops,'run') as run:
            with self.assertRaisesRegex(RuntimeError, '备用'): ops.control(dict(role='standby'), Path('/test'), 'start')
            run.assert_not_called()
    def test_safe_archive_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'test.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                info=tarfile.TarInfo('data/problem/1001/1.in');payload=b'123\n';info.size=len(payload);t.addfile(info,io.BytesIO(payload))
            with patch.object(ops,'owned'),patch.object(ops,'inspect_containers',return_value=[]):
                ops.restore({},root,{'archive':str(archive),'sha256':ops.file_hash(archive)})
            self.assertEqual((root/'data/problem/1001/1.in').read_bytes(),b'123\n')
    def test_archive_traversal_and_external_links_rejected(self):
        for name,link in [('data/../../escape',''),('data/link','/etc/passwd'),('/tmp/escape','')]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive=root/'bad.tar.gz'
                with tarfile.open(archive,'w:gz') as t:
                    info=tarfile.TarInfo(name)
                    if link:info.type=tarfile.SYMTYPE;info.linkname=link
                    t.addfile(info)
                with patch.object(ops,'owned'),patch.object(ops,'inspect_containers',return_value=[]):
                    with self.assertRaises(RuntimeError):ops.restore({},root,{'archive':str(archive),'sha256':ops.file_hash(archive)})
                self.assertFalse((root/'data').exists())
    def test_existing_data_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'data/important').write_text('keep')
            with patch.object(ops,'owned'),patch.object(ops,'inspect_containers',return_value=[]):
                with self.assertRaises(RuntimeError):ops.restore({},root,{'archive':'unused','sha256':'x'})
            self.assertEqual((root/'data/important').read_text(),'keep')
    def test_mirror_restores_on_exception_and_explicitly_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);daemon=root/'daemon.json';daemon.write_text('{"log-driver":"json-file"}')
            original=daemon.read_bytes();calls=[];snapshots=[];realpath=Path
            def path(p):return daemon if str(p)=='/etc/docker/daemon.json' else realpath(p)
            def run(args,**kw):
                calls.append(args)
                if args[:2]==['systemctl','reload']:snapshots.append(json.loads(daemon.read_text()))
                return ''
            with patch.object(ops,'Path',path),patch.object(ops,'run',run):
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    with ops.mirror_scope('https://mirror.example',root):raise RuntimeError('injected')
            self.assertEqual(daemon.read_bytes(),original)
            self.assertEqual(snapshots[-1]['registry-mirrors'],[])
            self.assertEqual(snapshots[-1]['log-driver'],'json-file')
    def test_spare_cannot_start_business(self):
        with patch.object(ops,'owned'),patch.object(ops,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'备用'):ops.deploy({'role':'standby'}, {},Path('/tmp/not-used'))
            run.assert_not_called()
    def test_backup_refuses_running_database(self):
        with patch.object(ops,'owned'),patch.object(ops,'inspect_containers',return_value=[{'running':True}]):
            with self.assertRaisesRegex(RuntimeError,'冷备份'):ops.snapshot({},Path('/tmp/not-used'),{})
    def test_never_sleep_records_masks_and_restores_only_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'private').mkdir();states={'sleep.target':'masked'};calls=[]
            def command(args,**kwargs):
                calls.append(args)
                if args[:2]==['systemctl','is-enabled']:return states.get(args[2],'static')
                if args[:2]==['systemctl','mask']:
                    for target in args[2:]:states[target]='masked'
                if args[:2]==['systemctl','unmask']:states[args[2]]='static'
                return ''
            with patch.object(ops,'run',command):
                ops.tune({},dict(neverSleep=True),root)
                original=json.loads((root/'private/host-settings.json').read_text())
                self.assertEqual(original['sleepTargets']['sleep.target'],'masked')
                self.assertEqual(original['sleepTargets']['suspend.target'],'static')
                ops.tune({},dict(neverSleep=True),root)
                self.assertEqual(json.loads((root/'private/host-settings.json').read_text()),original)
                with patch.object(ops,'owned'):
                    ops.main(dict(node={'path':str(root)},config={},action='restoreHost'))
            self.assertEqual(states['sleep.target'],'masked')
            self.assertEqual(states['suspend.target'],'static')
            self.assertNotIn(['systemctl','restart','systemd-logind'],calls)
    def test_sleep_unselected_does_not_change_host(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(ops,'run') as run:
            ops.tune({},dict(neverSleep=False),Path(tmp));run.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
