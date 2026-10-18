from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssh_alias import install_alias
from storage import Store


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state with spaces')
        self.store.key('webtest')
        self.ssh = self.root / 'ssh'; self.ssh.mkdir()
        self.node = dict(keyName='webtest', host='192.0.2.20', user='operator', sshPort=2222)

    def tearDown(self): self.temp.cleanup()

    def effective(self, alias):
        p = subprocess.run(['ssh', '-F', str(self.ssh / 'config'), '-G', alias], capture_output=True, text=True, check=True)
        return p.stdout

    def test_openssh_reads_alias_key_and_trust_with_spaces(self):
        original = b'# Keep existing settings\r\nHost other\r\n HostName example.com\r\n User original\r\nHost *\r\n User fallback\r\n'
        (self.ssh / 'config').write_bytes(original)
        self.assertEqual(install_alias(self.node, self.store, self.ssh), 'ssh webtest')
        text = self.effective('webtest')
        for line in ('hostname 192.0.2.20\n', 'user operator\n', 'port 2222\n', 'identitiesonly yes\n'):
            self.assertIn(line, text)
        self.assertIn((self.store.root / 'keys/webtest').as_posix(), text)
        self.assertIn((self.store.root / 'known_hosts').as_posix(), text)
        self.assertIn('user original\n', self.effective('other'))
        self.assertTrue((self.ssh / 'config').read_bytes().endswith(original))
        self.assertEqual(next(self.ssh.glob('config.before-*')).read_bytes(), original)

    def test_repeat_and_update_only_own_block(self):
        install_alias(self.node, self.store, self.ssh)
        before = (self.ssh / 'config').read_bytes()
        install_alias(self.node, self.store, self.ssh)
        self.assertEqual(before, (self.ssh / 'config').read_bytes())
        changed = dict(self.node, host='192.0.2.88', user='newuser', sshPort=2200)
        install_alias(changed, self.store, self.ssh)
        self.assertEqual((self.ssh / 'config').read_text().count('# BEGIN CSGOJ SSH webtest'), 1)
        for line in ('hostname 192.0.2.88\n', 'user newuser\n', 'port 2200\n'):
            self.assertIn(line, self.effective('webtest'))

    def test_conflicting_manual_alias_is_preserved(self):
        original = b'Host webtest other\n HostName other.example\n User other\n'
        (self.ssh / 'config').write_bytes(original)
        with self.assertRaisesRegex(ValueError, '原配置未覆盖'):
            install_alias(self.node, self.store, self.ssh)
        self.assertEqual((self.ssh / 'config').read_bytes(), original)

    def test_global_options_keep_scope(self):
        (self.ssh / 'config').write_text('ServerAliveCountMax 7\nHost other\n HostName example.com\n')
        install_alias(self.node, self.store, self.ssh)
        self.assertIn('serveralivecountmax 7\n', self.effective('other'))

    def test_matching_existing_alias_can_be_managed(self):
        key = (self.store.root / 'keys/webtest').as_posix()
        (self.ssh / 'config').write_text(f'Host webtest\n HostName 192.0.2.20\n User operator\n Port 2222\n IdentityFile "{key}"\n')
        install_alias(self.node, self.store, self.ssh)
        install_alias(dict(self.node, host='192.0.2.99'), self.store, self.ssh)
        self.assertIn('hostname 192.0.2.99\n', self.effective('webtest'))

    def test_imported_copy_of_same_private_key_is_not_a_conflict(self):
        original_key = self.ssh / 'webtest'
        original_key.write_bytes((self.store.root / 'keys/webtest').read_bytes())
        (self.ssh / 'config').write_text(f'Host webtest\n HostName 192.0.2.20\n User operator\n Port 2222\n IdentityFile "{original_key.as_posix()}"\n')
        install_alias(self.node, self.store, self.ssh)
        self.assertIn((self.store.root / 'keys/webtest').as_posix(), self.effective('webtest'))


if __name__ == '__main__': unittest.main()
