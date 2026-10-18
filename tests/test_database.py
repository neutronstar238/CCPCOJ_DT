import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine, node_hash
from models import normalize, protect_managed
from test_console import config

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.e=Engine(Mock());self.e.c=config()
        for n in self.e.c['nodes']:n.update(prepared=True,deployed=True)
        self.n=self.e.c['nodes'][0]
        self.e.save=Mock();self.e.freeze=Mock();self.e.resume=Mock();self.e.client_factory=Mock();self.e.sql=Mock(return_value='0')
        self.plan=dict(target='/data-ssd/mysql/ccpc',fingerprint='pin',nodeHash=node_hash(self.n))
        self.e.state['nodes'][self.n['id']]={'databasePlan':self.plan}
        self.e.op=Mock(side_effect=lambda n,action,*args:copy.deepcopy(self.plan) if action=='databaseCheck' else {})
    def test_path_validated_and_locked_after_prepare(self):
        for p in ('/','/etc/mysql','/data-ssd/../etc/mysql','/home/ccpc00/cpc/db','/mnt/x;echo'):
            c=config();c['nodes'][0]['databasePath']=p
            with self.subTest(p=p),self.assertRaises(ValueError):normalize(c)
        old=config();new=copy.deepcopy(old);new['nodes'][0]['databasePath']='/data-ssd/mysql/ccpc'
        normalize(new);old['nodes'][0]['prepared']=True
        with self.assertRaises(ValueError):protect_managed(old,new)
    def test_confirmation_required_at_api_engine_boundary(self):
        for flag in (None,False,1,'true'):
            with self.assertRaisesRegex(ValueError,'二次确认'):
                self.e.start('databaseMigrate',self.e.c,[self.n['id']],dict(confirmed=flag))
        self.e.op.assert_not_called()
    def test_success_commits_before_reopening_web(self):
        events=[]
        self.e.op.side_effect=lambda n,a,*args:events.append(a) or (self.plan if a=='databaseCheck' else {})
        self.e.resume.side_effect=lambda *a:events.append('resume')
        before=copy.deepcopy(self.n['secrets'])
        self.e.database_action('databaseMigrate',self.n,dict(target=self.plan['target']))
        self.assertLess(events.index('databaseValidate'),events.index('databaseCommit'))
        self.assertLess(events.index('databaseCommit'),events.index('resume'))
        self.assertEqual(self.n['secrets'],before)
        self.assertEqual(self.n['databasePath'],self.plan['target'])
    def test_invalid_new_database_rolls_back_then_resumes(self):
        events=[]
        def operation(n,a,*args):
            events.append(a)
            if a=='databaseValidate':raise RuntimeError('mysql failed')
            return self.plan if a=='databaseCheck' else {}
        self.e.op.side_effect=operation
        with self.assertRaisesRegex(RuntimeError,'mysql failed'):
            self.e.database_action('databaseMigrate',self.n,dict(target=self.plan['target']))
        self.assertIn('databaseRollback',events);self.e.resume.assert_called_once()
        self.assertNotIn('databasePath',self.n)
    def test_failure_after_commit_never_rolls_back(self):
        self.e.resume.side_effect=RuntimeError('network')
        with self.assertRaisesRegex(RuntimeError,'network'):
            self.e.database_action('databaseMigrate',self.n,dict(target=self.plan['target']))
        self.assertNotIn('databaseRollback',[c.args[1] for c in self.e.op.call_args_list])
    def test_changed_destination_or_mount_does_not_stop_server(self):
        with self.assertRaisesRegex(ValueError,'先检查'):
            self.e.database_action('databaseMigrate',self.n,dict(target='/other/mysql'))
        self.e.op.return_value={'fingerprint':'changed'};self.e.op.side_effect=None
        with self.assertRaisesRegex(ValueError,'变化'):
            self.e.database_action('databaseMigrate',self.n,dict(target=self.plan['target']))
        self.e.freeze.assert_not_called()
    def test_recovery_after_commit_syncs_without_stopping(self):
        self.e.op.side_effect=None;self.e.op.return_value=dict(pending=False,managedStorage=dict(target=self.plan['target']))
        self.e.database_action('databaseRecover',self.n,{})
        self.e.freeze.assert_not_called();self.assertEqual(self.n['databasePath'],self.plan['target'])

if __name__=='__main__':unittest.main()
