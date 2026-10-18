import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from engine import Engine
from test_console import config

class BatchUpdates(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.e=Engine(Mock(root=Path(self.temp.name)));self.e.c=config()
        for n in self.e.c['nodes']:
            n.update(prepared=True,deployed=True,targetVersion='2.0.288-exp')
        self.web,self.judge=self.e.c['nodes'];self.web['targetVersion']='2.0.287-exp'
        self.e.op=Mock(side_effect=lambda n,a,*args,**kw:self.inventory(n))
        self.e.upgrade=Mock();self.e.prepare=Mock();self.e.activate=Mock();self.e.save=Mock()
    def inventory(self,n,mode='update'):
        refs=['csgrandeur/ccpcoj-'+('web2' if n['kind']=='web' else 'judge2')+':'+n['targetVersion']]
        if n['kind']=='web':refs+=['mysql:8.0.32','nginx:1.29.7-alpine','phpmyadmin:5.2.3']
        return dict(mode=mode,managed=True,reason=mode,currentVersions=['2.0.281-exp'],availableVersions=[n['targetVersion']],images=[dict(ref=ref) for ref in refs])
    def test_existing_uses_per_node_versions_web_first_no_initialize(self):
        seen=[];self.e.upgrade.side_effect=lambda n,**kw:seen.append((n['id'],self.e.c['version'],kw))
        original=copy.deepcopy(self.e.c)
        self.e.batch_update([self.judge['id'],self.web['id']])
        self.assertEqual(seen,[(self.web['id'],'2.0.287-exp',{'prefer_remote':True}),(self.judge['id'],'2.0.288-exp',{'prefer_remote':True})])
        self.e.prepare.assert_not_called();self.e.activate.assert_not_called()
        self.assertEqual(self.e.c,original)
    def test_existing_remote_images_do_not_need_local_cache(self):
        self.e.distribute_images=Mock();self.e.c['version']=self.judge['targetVersion']
        self.e.ensure_images(self.judge);self.e.distribute_images.assert_not_called()
    def test_only_missing_refs_are_distributed(self):
        self.e.op.return_value={};self.e.op.side_effect=lambda *a,**kw:dict(images=[])
        self.e.distribute_images=Mock();self.e.c['version']=self.judge['targetVersion']
        self.e.ensure_images(self.judge)
        self.assertEqual(self.e.distribute_images.call_args.kwargs['refs'],['csgrandeur/ccpcoj-judge2:2.0.288-exp'])
    def test_fresh_node_initializes_only(self):
        self.judge.update(prepared=False,deployed=False)
        self.e.c['tool']={'sha256':'fixture'}
        self.e.op.side_effect=lambda n,*a,**kw:self.inventory(n,'initialize')
        self.e.batch_update([self.judge['id']])
        self.e.prepare.assert_called_once_with(self.judge,prefer_remote=True)
        self.e.activate.assert_called_once_with(self.judge);self.e.upgrade.assert_not_called()
    def test_fresh_standby_does_not_activate(self):
        self.judge.update(prepared=False,deployed=False,role='standby');self.e.c['tool']={'sha256':'fixture'}
        self.e.op.side_effect=lambda n,*a,**kw:self.inventory(n,'initialize')
        self.e.batch_update([self.judge['id']]);self.e.activate.assert_not_called()
    def test_residual_data_blocks_whole_batch_before_replacement(self):
        self.e.op.side_effect=lambda n,*a,**kw:self.inventory(n,'blocked' if n['kind']=='judge' else 'update')
        with self.assertRaises(ValueError):self.e.batch_update([self.web['id'],self.judge['id']])
        self.e.upgrade.assert_not_called();self.e.prepare.assert_not_called()
    def test_missing_configuration_on_previously_deployed_node_is_not_fresh(self):
        self.e.op.side_effect=lambda n,*a,**kw:self.inventory(n,'initialize')
        with self.assertRaisesRegex(ValueError,'不会重新初始化'):self.e.batch_update([self.judge['id']])
        self.e.prepare.assert_not_called()
    def test_automatic_adoption_runs_inspect_then_commit(self):
        self.judge['prepared']=False
        inv=self.inventory(self.judge,'adoptUpdate');inv['managed']=False
        self.e.op.side_effect=lambda *a,**kw:inv;self.e.adopt_existing=Mock()
        self.e.batch_update([self.judge['id']])
        self.assertEqual([c.args[1] for c in self.e.adopt_existing.call_args_list],[False,True])
        self.e.upgrade.assert_called_once()
    def test_sync_failure_is_not_displayed_as_empty_server(self):
        self.e.op.side_effect=RuntimeError('SSH offline')
        with self.assertRaises(RuntimeError):self.e.sync_versions([self.judge['id']])
        self.assertEqual(self.e.state['nodes'][self.judge['id']]['inventory']['mode'],'blocked')
    def test_batch_stops_after_failure_and_restores_global_version(self):
        old=self.e.c['version'];self.e.upgrade.side_effect=RuntimeError('failed')
        with self.assertRaises(RuntimeError):self.e.batch_update([self.web['id'],self.judge['id']])
        self.assertEqual(self.e.upgrade.call_count,1);self.assertEqual(self.e.c['version'],old)

if __name__=='__main__':unittest.main()
