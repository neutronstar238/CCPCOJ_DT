from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine
from models import normalize, protect_managed
from test_console import config
import copy

class PodsTests(unittest.TestCase):
    def setUp(self):
        self.e=Engine(Mock());self.e.c=config();self.n=self.e.c['nodes'][1]
        self.n.update(prepared=True,deployed=True,preparedVersion=self.e.c['version'],deployedVersion=self.e.c['version'],desiredPods=2)
        self.e.op=Mock(return_value={});self.e.sql=Mock(return_value='0');self.e.client_factory=Mock()
        def activate(n):
            self.e.state['nodes'][n['id']]=dict(status='checked',containers=[dict(running=True)]*n['pods'])
        self.e.activate=Mock(side_effect=activate)
    def test_target_saves_without_lying_about_current_pods(self):
        old=copy.deepcopy(self.e.c);old['nodes'][1].pop('desiredPods')
        c=protect_managed(old,normalize(self.e.c))
        self.assertEqual(c['nodes'][1]['pods'],1);self.assertEqual(c['nodes'][1]['desiredPods'],2)
    def test_resize_updates_current_count_after_activation(self):
        self.e.resize_judge(self.n)
        self.assertEqual(self.n['pods'],2)
        self.assertEqual([c.args[1] for c in self.e.op.call_args_list],['judgeResizeCheck','removeJudge'])
        self.e.activate.assert_called_once_with(self.n)
        self.assertIn('Web 心跳已更新',self.e.state['nodes'][self.n['id']]['serviceStatus'])
    def test_resource_failure_does_not_stop_original(self):
        self.e.op.side_effect=RuntimeError('resource shortfall')
        with self.assertRaises(RuntimeError): self.e.resize_judge(self.n)
        self.assertEqual(self.n['pods'],1);self.assertEqual(self.e.op.call_count,1);self.e.activate.assert_not_called()
    def test_busy_queue_does_not_stop_original(self):
        self.e.sql.return_value='1'
        with self.assertRaisesRegex(ValueError,'队列'): self.e.resize_judge(self.n)
        self.assertEqual(self.n['pods'],1);self.assertEqual(self.e.op.call_count,1)
    def test_failed_activation_rolls_back_original_count(self):
        self.e.activate.side_effect=[RuntimeError('failed new pods'),None]
        with self.assertRaises(RuntimeError): self.e.resize_judge(self.n)
        self.assertEqual(self.n['pods'],1);self.assertEqual(self.n['desiredPods'],2)
        self.assertEqual(self.e.activate.call_count,2)
    def test_standby_only_updates_plan(self):
        self.n.update(role='standby',deployed=False)
        self.e.resize_judge(self.n)
        self.assertEqual(self.n['pods'],2);self.e.activate.assert_not_called();self.e.sql.assert_not_called()
    def test_resize_refuses_implicit_image_upgrade(self):
        self.e.c['version']='2.0.999-exp'
        with self.assertRaisesRegex(ValueError,'不同时升级'):self.e.resize_judge(self.n)
        self.e.op.assert_not_called()

if __name__=='__main__':unittest.main()
