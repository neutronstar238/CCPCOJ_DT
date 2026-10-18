import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from local_deploy import LocalDeployment, LocalEngine, LocalStore, wsl_list
from models import normalize


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.parent=Mock(root=Path(self.tmp.name));self.parent.load.return_value={}
        self.local=LocalDeployment(self.parent)
        self.c=self.local.config()
        self.local.store=Mock();self.local.store.load.side_effect=lambda:copy.deepcopy(self.c)
        self.local.store.save.side_effect=lambda c:setattr(self,'c',copy.deepcopy(c))
    def test_default_is_independent_and_versions_blank(self):
        self.assertFalse(self.c['disableGui']);self.assertFalse(self.c['neverSleep'])
        self.assertEqual([n['id'] for n in self.c['nodes']],['localweb','localpcj'])
        self.assertTrue(all(not n.get('targetVersion') for n in self.c['nodes']))
    def test_settings_validate_paths_and_auto_save_versions(self):
        for path in ('/etc','/srv/../../etc','C:\\data'):
            with self.assertRaises(ValueError):self.local.save_settings(dict(path=path))
        c=self.local.save_settings(dict(distro='Ubuntu-24.04',path='/mnt/d/local-oj',webVersion='2.0.292-exp',judgeVersion='2.0.291-exp'))
        self.assertEqual(c['nodes'][0]['path'],'/mnt/d/local-oj/web')
        self.assertEqual(c['nodes'][1]['targetVersion'],'2.0.291-exp')
    def test_prepared_identity_cannot_be_retargeted(self):
        self.c['nodes'][0]['prepared']=True
        for settings in (dict(distro='other'),dict(path='/srv/new'),dict(port=80)):
            with self.assertRaises(ValueError):self.local.save_settings(settings)
        self.local.save_settings(dict(pods=2,webVersion='2.0.293-exp'))
        self.assertEqual(self.c['nodes'][1]['pods'],2)
    def test_local_vault_does_not_write_remote_config(self):
        p=self.parent.root/'config.dpapi';p.write_bytes(b'remote')
        with patch('local_deploy.dpapi',side_effect=lambda b,*a:b),patch('local_deploy.restrict'):
            store=LocalStore(self.parent);store.save(self.c)
            self.assertEqual(store.load(),self.c)
        self.assertEqual(p.read_bytes(),b'remote')
    def test_no_implicit_selection_or_unconfirmed_update(self):
        self.c['local']['distro']='Ubuntu'
        with patch('local_deploy.wsl_list',return_value=['Ubuntu']):
            for selected,confirmed in (([],True),(['localweb'],False),(['remote'],True)):
                with self.assertRaises(ValueError):self.local.start('batchUpdate',selected,confirmed)
    def test_desktop_distro_not_listed(self):
        result=Mock(returncode=0,stdout='Ubuntu\r\ndocker-desktop\r\n'.encode('utf-16-le'))
        with patch('local_deploy.subprocess.run',return_value=result):self.assertEqual(wsl_list(),['Ubuntu'])
    def test_multi_start_orders_web_first(self):
        e=LocalEngine(Mock());e.c=self.c;e.c['localStart']=True;e.start_service=Mock()
        e.health(['localpcj','localweb'])
        self.assertEqual([x.args[0]['kind'] for x in e.start_service.call_args_list],['web','judge'])
    def test_inventory_survives_health_failure(self):
        e=LocalEngine(Mock());e.c=self.c;e.state['nodes']['localweb']={'inventory':{'mode':'update'}}
        with patch('engine.Engine.health',side_effect=RuntimeError('stopped')):
            with self.assertRaises(RuntimeError):e.health(['localweb'])
        self.assertEqual(e.state['nodes']['localweb']['inventory']['mode'],'update')
    def test_external_upgrade_rolls_back_on_failed_check(self):
        e=LocalEngine(Mock());e.c=self.c;e.c['local']['judgeMode']='external';n=e.c['nodes'][1]
        e.check_update=Mock(return_value={'updateAvailable':True,'targetId':'sha256:test'})
        e.op=Mock();e.verify_service=Mock(side_effect=RuntimeError('bad'))
        with self.assertRaises(RuntimeError):e.upgrade(n,True)
        self.assertEqual([x.args[1] for x in e.op.call_args_list],['stop','updateBegin','start','updateRollback','start'])
    def test_keepalive_is_reused_while_process_is_alive(self):
        e=LocalEngine(Mock());p=Mock();p.poll.return_value=None
        with patch('local_deploy.subprocess.Popen',return_value=p) as launch:
            e.keep_alive('Ubuntu');e.keep_alive('Ubuntu')
            self.assertEqual(launch.call_count,1)
            self.assertEqual(launch.call_args.kwargs['cwd'],e.store.root)
            args=launch.call_args.args[0];self.assertIn('flock',args);self.assertEqual(args[-2:],['sleep','infinity'])
    def test_stop_routes_only_explicit_local_selection(self):
        self.c['local']['distro']='Ubuntu';self.local.engine.start_local=Mock()
        with patch('local_deploy.wsl_list',return_value=['Ubuntu']):
            for selected in ([],['remote']):
                with self.assertRaises(ValueError):self.local.start('stop',selected)
            self.local.start('stop',['localpcj'])
        self.assertEqual(self.local.engine.start_local.call_args.args[2],['localpcj'])
    def test_stop_orders_judge_first_and_retains_config(self):
        e=LocalEngine(Mock());e.c=self.c
        for n in e.c['nodes']:n.update(prepared=True,deployed=True)
        before=copy.deepcopy(e.c);e.op=Mock();e.health=Mock()
        e.state['nodes']={n['id']:{'containers':[{'running':False}]} for n in e.c['nodes']}
        e.stop_services(['localweb','localpcj'])
        self.assertEqual([(c.args[0]['id'],c.args[1]) for c in e.op.call_args_list],[('localpcj','stop'),('localweb','stop')])
        self.assertEqual(e.c,before)
    def test_stop_checks_all_nodes_before_stopping_any(self):
        e=LocalEngine(Mock());e.c=self.c;e.op=Mock()
        e.c['nodes'][1].update(prepared=True,deployed=True)
        with self.assertRaises(ValueError):e.stop_services(['localpcj','localweb'])
        e.op.assert_not_called()
    def test_stop_does_not_claim_success_when_container_stays_running(self):
        e=LocalEngine(Mock());e.c=self.c;e.op=Mock();e.health=Mock()
        e.c['nodes'][1].update(prepared=True,deployed=True)
        e.state['nodes']['localpcj']={'containers':[{'running':True}]}
        with self.assertRaisesRegex(RuntimeError,'仍有容器运行'):e.stop_services(['localpcj'])
    def test_inspection_accepts_stopped_but_start_verification_does_not(self):
        e=LocalEngine(Mock());e.c=self.c;n=e.c['nodes'][1];n.update(prepared=True,deployed=True)
        remote=Mock();e.remote=Mock(return_value=remote)
        remote.op.return_value={'containers':[{'running':False}], 'dockerRunning':True,'prepared':True,'runningPods':0,'totalPods':1}
        e.health(['localpcj'],allow_stopped=True)
        self.assertEqual(e.state['nodes']['localpcj']['serviceStatus'],'已关闭')
        with self.assertRaises(RuntimeError):e.health(['localpcj'])
    def test_inspection_still_rejects_partial_or_extra_containers(self):
        e=LocalEngine(Mock());e.c=self.c;n=e.c['nodes'][0];n.update(prepared=True,deployed=True)
        remote=Mock();e.remote=Mock(return_value=remote)
        remote.op.return_value={'containers':[{'running':i==0} for i in range(4)],'dockerRunning':True}
        with self.assertRaises(RuntimeError):e.health(['localweb'],allow_stopped=True)

if __name__=='__main__':unittest.main()
