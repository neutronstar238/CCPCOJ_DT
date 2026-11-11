import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import paramiko
from arena_ssh import check_key, accept_hosts, host_name, known_hosts_text
from arena_deploy import ArenaDeployment
from test_arena import sample


class ArenaTrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = paramiko.RSAKey.generate(1024)
        self.record = dict(host='192.0.2.10', algorithm=self.key.get_name(),
                           publicKey=self.key.get_base64(), fingerprint=self.key.fingerprint, trusted=False)
        self.sources = patch('arena_ssh.known_sources', side_effect=lambda root: [root/'known_hosts', root/'arena_known_hosts'])
        self.sources.start(); self.addCleanup(self.sources.stop)

    def test_first_use_pin_and_changed_key_rejected_without_overwrite(self):
        self.assertFalse(check_key(self.root, self.record['host'], self.key))
        with patch('arena_ssh.restrict'): accept_hosts(self.root, [self.record])
        original = (self.root/'arena_known_hosts').read_bytes()
        self.assertTrue(check_key(self.root, self.record['host'], self.key))
        changed = paramiko.RSAKey.generate(1024)
        with self.assertRaisesRegex(ValueError, '不一致'):
            check_key(self.root, self.record['host'], changed)
        bad = dict(self.record, publicKey=changed.get_base64())
        with patch('arena_ssh.restrict'), self.assertRaisesRegex(ValueError, '不一致'):
            accept_hosts(self.root, [bad])
        self.assertEqual((self.root/'arena_known_hosts').read_bytes(), original)

    def test_nonstandard_port_and_shared_windows_trust(self):
        name = host_name(dict(host='192.0.2.10',port=2222))
        self.assertEqual(name, '[192.0.2.10]:2222')
        record = dict(self.record, host=name)
        (self.root/'known_hosts').write_text(known_hosts_text([record]))
        self.assertTrue(check_key(self.root, name, self.key))
        self.assertFalse(check_key(self.root, '192.0.2.10', self.key))

    def test_unknown_host_stops_before_key_transfer_or_ansible_and_resumes_exact_job(self):
        manager = ArenaDeployment(Mock(root=self.root)); c=sample()
        manager.state['id']='job1'; manager.running=True
        with patch.object(manager,'config',return_value=c), patch('arena_deploy.network.snapshot',return_value={}), \
             patch('arena_deploy.inspect_hosts',return_value=[self.record]), patch('arena_deploy.prepare_keys') as keys, \
             patch('arena_deploy.subprocess.Popen') as process:
            manager._run(c,'server','status')
            keys.assert_not_called(); process.assert_not_called()
            self.assertEqual(manager.status()['status'],'等待首次 SSH 指纹确认')
            self.assertNotIn('publicKey', str(manager.status()))
            with self.assertRaisesRegex(ValueError,'过期'): manager.trust('old-job')
            changed=copy.deepcopy(c); changed['servers'][0]['host']='192.0.2.11'
            with patch.object(manager,'config',return_value=changed), self.assertRaisesRegex(ValueError,'变化'):
                manager.trust('job1')
            with patch('arena_ssh.restrict'), patch.object(manager,'start',return_value={'ok':True}) as start:
                manager.trust('job1'); start.assert_called_once_with('server','status')
            self.assertTrue(check_key(self.root,self.record['host'],self.key))
            with self.assertRaisesRegex(ValueError,'过期'): manager.trust('job1')


if __name__=='__main__': unittest.main()
