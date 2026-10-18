import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from test_console import config, web, judge

class ServiceStatusTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine(Mock())
        self.e.c = config()
        self.n = self.e.c['nodes'][0]
        self.n.update(prepared=True, deployed=True)
        self.remote = Mock()
        self.e.remote = Mock(return_value=self.remote)
        self.payload = dict(prepared=True, dockerRunning=True, images=[], containers=[dict(name=str(i), running=True) for i in range(4)])
        self.remote.op.side_effect = lambda *a, **k: copy.deepcopy(self.payload)
        self.e.client_factory = Mock()
    def health(self, fail=False):
        if fail:
            with self.assertRaises(RuntimeError): self.e.health(['web01'])
        else: self.e.health(['web01'])
        return self.e.state['nodes']['web01']
    def test_online_server_stopped_service_can_start(self):
        for c in self.payload['containers']: c['running'] = False
        result = self.health(True)
        self.assertTrue(result['online']); self.assertTrue(result['canStart'])
        self.assertEqual(result['serviceStatus'], '未运行')
        self.e.client_factory.assert_not_called()
    def test_missing_containers_require_deployment(self):
        self.payload['containers'].pop()
        result = self.health(True)
        self.assertFalse(result['canStart']); self.assertIn('容器缺失', result['serviceStatus'])
    def test_daemon_down_not_server_offline(self):
        self.payload.update(dockerRunning=False, containers=[])
        result = self.health(True)
        self.assertTrue(result['online']); self.assertTrue(result['canStart'])
        self.assertEqual(result['serviceStatus'], 'Docker 未运行')
    def test_application_failure_preserves_online_and_running(self):
        self.e.client_factory.return_value.verify.side_effect = RuntimeError('HTTP 502')
        result = self.health(True)
        self.assertTrue(result['online']); self.assertIn('容器运行', result['serviceStatus'])
        self.assertFalse(result['canStart'])
    def test_sudo_failure_preserves_ssh_online(self):
        self.remote.op.side_effect = RuntimeError('sudo failed')
        result = self.health(True)
        self.assertTrue(result['online']); self.assertIn('检测未完成', result['serviceStatus'])
    def test_failed_ssh_does_not_claim_host_powered_off(self):
        self.e.remote.side_effect = RuntimeError('authentication failed')
        result = self.health(True)
        self.assertIn('认证', result['serverStatus']); self.assertNotIn('online', result)
    def test_standby_cannot_start(self):
        self.n['role'] = 'standby'
        self.payload['containers'] = []
        result = self.health()
        self.assertEqual(result['serviceStatus'], '未启动（备用）'); self.assertFalse(result['canStart'])
        with self.assertRaises(ValueError): self.e.start_service(self.n)
    def test_unexpected_extra_judge_pods_are_reported(self):
        j=self.e.c['nodes'][1];j.update(prepared=True,deployed=True)
        self.payload.update(containers=[dict(name='judge-pcj01',running=True)],runningPods=2,totalPods=2)
        with self.assertRaises(RuntimeError):self.e.health([j['id']])
        s=self.e.state['nodes'][j['id']]
        self.assertEqual(s['runningPods'],2)
        self.assertIn('配置数量之外',s['serviceStatus'])
        self.e.client_factory.assert_not_called()
    def test_start_runs_verification_and_reports_real_status(self):
        self.e.op = Mock(return_value={})
        self.e.start_service(self.n)
        self.e.op.assert_called_once_with(self.n, 'start')
        self.e.client_factory.return_value.verify.assert_called_once()
        self.assertIn('正常', self.e.state['nodes']['web01']['serviceStatus'])
    def test_judge_checks_fresh_heartbeat(self):
        j = self.e.c['nodes'][1]; j.update(prepared=True, deployed=True)
        self.payload['containers'] = [dict(name='judge-pcj01', running=True)]
        cli = self.e.client_factory.return_value.login.return_value
        cli.judges.return_value = [dict(user_id=j['judgeUser'], accesstime='old-time')]
        self.e.health(['pcj01'])
        cli.heartbeat.assert_called_once_with(j, 'old-time')
        self.assertIn('心跳更新', self.e.state['nodes']['pcj01']['serviceStatus'])

if __name__ == '__main__': unittest.main()
