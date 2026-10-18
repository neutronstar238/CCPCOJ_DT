import copy
import unittest
from unittest.mock import MagicMock
from test_console import config,web,judge
from engine import Engine

class AdoptionChecks(unittest.TestCase):
    def setUp(self):
        self.e=Engine(MagicMock())
        self.e.c=config([web(),judge()]);self.n=self.e.c['nodes'][0]
        self.e.op=MagicMock(return_value=dict(fingerprint='stable',version='2.0.281-exp',containers=[]))
        self.e.client_factory=MagicMock();self.e.save=MagicMock();self.e.health=MagicMock()
    def test_inspection_does_not_mark_or_stop(self):
        self.e.adopt_existing(self.n)
        self.assertFalse(self.n.get('prepared'))
        self.assertEqual([c.args[1] for c in self.e.op.call_args_list],['adoptInspect'])
        self.e.client_factory.return_value.verify.assert_called_once()
    def test_commit_requires_prior_inspection(self):
        with self.assertRaisesRegex(ValueError,'先检查'):self.e.adopt_existing(self.n,True)
        self.assertNotIn('adopt',[c.args[1] for c in self.e.op.call_args_list])
    def test_success_keeps_identity_and_marks_original_version(self):
        old=copy.deepcopy(self.n);self.e.adopt_existing(self.n);self.e.adopt_existing(self.n,True)
        self.assertTrue(self.n['adopted']);self.assertEqual(self.n['deployedVersion'],'2.0.281-exp')
        for k in old:self.assertEqual(self.n[k],old[k])
        self.e.health.assert_called_once_with([self.n['id']])
    def test_changed_config_requires_inspection_again(self):
        self.e.adopt_existing(self.n);self.n['port']+=1
        with self.assertRaisesRegex(ValueError,'重新检查'):self.e.adopt_existing(self.n,True)
    def test_changed_container_requires_inspection_again(self):
        self.e.adopt_existing(self.n);self.e.op.return_value['fingerprint']='changed'
        with self.assertRaisesRegex(ValueError,'重新检查'):self.e.adopt_existing(self.n,True)
    def test_bad_admin_login_never_marks(self):
        self.e.client_factory.return_value.verify.side_effect=RuntimeError('login failed')
        with self.assertRaises(RuntimeError):self.e.adopt_existing(self.n)
        self.assertNotIn('adoption',self.e.state['nodes'].get(self.n['id'],{}))
    def test_adopted_node_cannot_be_reinitialized(self):
        self.n['adopted']=True
        with self.assertRaisesRegex(ValueError,'不重复初始化'):self.e.deploy_all([self.n['id']])

if __name__=='__main__':unittest.main()
