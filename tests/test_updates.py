import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine
from test_console import config

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.e=Engine(Mock());self.e.c=config()
        for n in self.e.c['nodes']:n.update(prepared=True,deployed=True,preparedVersion='2.0.277-exp',deployedVersion='2.0.277-exp')
        self.web,self.judge=self.e.c['nodes']
        self.e.op=Mock(return_value={});self.e.sql=Mock(return_value='0');self.e.counts=Mock(return_value='3\t2\t1\t0')
        self.e.client_factory=Mock();self.e.verify_service=Mock();self.e.health=Mock();self.e.snapshot=Mock();self.e.freeze=Mock();self.e.resume=Mock()
        self.e.check_update=Mock(return_value=dict(updateAvailable=True,targetId='sha256:new'))
    def test_web_only_updates_app_and_retains_identity(self):
        original=copy.deepcopy(self.web)
        self.e.upgrade(self.web)
        self.e.snapshot.assert_called_once_with(self.web)
        actions=[c.args[1] for c in self.e.op.call_args_list]
        self.assertEqual(actions,['updateBegin','start','updateCommit','start'])
        for k in ['secrets','adminUser','site','port']:self.assertEqual(self.web[k],original[k])
        self.assertEqual(self.web['deployedVersion'],self.e.c['version'])
    def test_judge_keeps_account_pods_without_reregister(self):
        original=copy.deepcopy(self.judge);self.e.judge_account=Mock()
        self.e.upgrade(self.judge)
        self.e.judge_account.assert_not_called();self.e.snapshot.assert_not_called()
        for k in ['judgeUser','judgePassword','pods','web']:self.assertEqual(original[k],self.judge[k])
    def test_failed_verify_restores_original_version(self):
        self.e.verify_service.side_effect=RuntimeError('login failed')
        with self.assertRaises(RuntimeError):self.e.upgrade(self.web)
        self.assertIn('updateRollback',[c.args[1] for c in self.e.op.call_args_list])
        self.assertEqual(self.web['deployedVersion'],'2.0.277-exp')
        self.e.resume.assert_called_once()
    def test_missing_data_stops_commit(self):
        self.e.counts.side_effect=['3\t2\t1\t0','1\t0\t0\t0']
        with self.assertRaisesRegex(RuntimeError,'数量'):self.e.upgrade(self.web)
        self.assertNotIn('updateCommit',[c.args[1] for c in self.e.op.call_args_list])
    def test_same_digest_never_stops(self):
        self.e.check_update.return_value['updateAvailable']=False
        self.e.upgrade(self.web);self.e.op.assert_not_called();self.e.freeze.assert_not_called()
    def test_busy_queue_never_stops(self):
        self.e.sql.return_value='1'
        with self.assertRaisesRegex(ValueError,'队列'):self.e.upgrade(self.web)
        self.e.op.assert_not_called();self.e.freeze.assert_not_called()
    def test_standby_only_refreshes_image(self):
        self.web.update(role='standby',deployed=False)
        self.e.upgrade(self.web);self.e.op.assert_not_called();self.e.snapshot.assert_not_called()
        self.assertEqual(self.web['preparedVersion'],self.e.c['version']);self.assertFalse(self.web['deployed'])
    def test_failure_after_commit_does_not_rollback_live_database(self):
        self.e.health.side_effect=RuntimeError('judge offline')
        with self.assertRaises(RuntimeError):self.e.upgrade(self.web)
        self.assertNotIn('updateRollback',[c.args[1] for c in self.e.op.call_args_list])
        self.assertEqual(self.web['deployedVersion'],self.e.c['version'])

if __name__=='__main__':unittest.main()
