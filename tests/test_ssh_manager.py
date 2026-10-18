import copy
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import unittest
from unittest.mock import Mock, patch
import paramiko
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Store
from ssh_manager import SSHManager
from test_console import config
from ssh_check import Fixture

def profile(name='pcj01', host='192.0.2.10', user='ccpc00'):
    return dict(keyName=name, host=host, user=user, sshPort=22, password='test-pass')

def wait(manager):
    deadline = time.monotonic()+8
    while manager.running and time.monotonic()<deadline: time.sleep(.02)
    if manager.running: raise AssertionError('SSH job did not finish')
    return manager.status()

class SSHManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = Store(self.root/'state'); self.ssh = self.root/'ssh'; self.ssh.mkdir()
        self.m = SSHManager(self.store, ssh_dir=self.ssh)
    def tearDown(self): self.tmp.cleanup()
    def test_standalone_profiles_encrypted_and_generate_without_remote(self):
        self.m.remote_factory = Mock()
        self.m.save([profile()]); self.m.start('generate',['pcj01'])
        self.assertTrue(wait(self.m)['results']['pcj01']['ok'])
        self.m.remote_factory.assert_not_called()
        self.assertNotIn(b'test-pass',(self.store.root/'ssh-connections.dpapi').read_bytes())
        key = (self.store.root/'keys/pcj01').read_bytes()
        self.m.start('generate',['pcj01']); wait(self.m)
        self.assertEqual(key,(self.store.root/'keys/pcj01').read_bytes())
        self.assertIsNone(self.store.load())
    def test_edit_address_and_user_syncs_existing_node(self):
        c=config(); c['nodes'][1]['prepared']=True; self.store.save(c)
        key, _ = self.store.key('pcj01'); original = key.read_bytes()
        self.m.remote_factory = Mock()
        known = self.ssh/'known_hosts'; known.write_text('# preserve trust\n')
        # An imported manual alias uses the same key but the old address/account.
        (self.ssh/'config').write_text(f'Host pcj01\n HostName 192.0.2.10\n User ccpc00\n IdentityFile {key.as_posix()}\n')
        records=self.m.profiles()
        records[1].update(host='192.0.2.99',user='operator',sshPort=2222)
        self.m.save(records)
        saved=self.store.load()['nodes'][1]
        self.assertEqual((saved['host'],saved['user'],saved['sshPort']),('192.0.2.99','operator',2222))
        self.assertEqual(saved['keyName'],'pcj01'); self.assertTrue(saved['prepared'])
        native = subprocess.run(['ssh','-F',str(self.ssh/'config'),'-G','pcj01'], capture_output=True,text=True,check=True).stdout
        for line in ('hostname 192.0.2.99\n','user operator\n','port 2222\n'): self.assertIn(line,native)
        self.assertEqual(key.read_bytes(),original)
        self.assertEqual(known.read_text(),'# preserve trust\n')
        self.m.remote_factory.assert_not_called()
        self.assertIn('未验证', self.m.status()['results']['pcj01']['status'])
    def test_conflict_does_not_partially_save_aliases_or_connections(self):
        self.store.key('pcj01'); self.store.key('web01')
        self.m.save([profile()])
        old_records = self.m.profiles()
        cfg = self.ssh/'config'
        cfg.write_text(cfg.read_text() + '\nHost web01\n HostName unrelated.example\n User another\n')
        original = cfg.read_bytes()
        with self.assertRaisesRegex(ValueError, '原配置未覆盖'):
            self.m.save([profile(host='192.0.2.99'), profile(name='web01')])
        self.assertEqual(cfg.read_bytes(), original)
        self.assertEqual(self.m.profiles(), old_records)

    def test_inventory_import_preserves_source_and_existing_profile(self):
        source=Store(self.root/'source'); key,_=source.key('pcj01')
        original=key.read_bytes()
        (self.ssh/'config').write_text(f'Host pcj01\n HostName 192.0.2.33\n User ccpc00\n IdentityFile {key.as_posix()}\nHost unrelated\n HostName example.com\n',encoding='utf-8')
        self.assertEqual([p['keyName'] for p in self.m.inventory()],['pcj01'])
        self.m.save([profile(host='192.0.2.55')]); self.m.import_existing(['pcj01'])
        self.assertEqual(self.m.profiles()[0]['host'],'192.0.2.55')
        self.assertEqual(key.read_bytes(),original)
        self.assertEqual((self.store.root/'keys/pcj01').read_bytes(),original)
        self.assertTrue((self.store.root/'keys/pcj01.pub').exists())
    def test_same_name_different_key_never_overwrites(self):
        source=Store(self.root/'source'); key,_=source.key('pcj01')
        self.store.key('pcj01'); old=(self.store.root/'keys/pcj01').read_bytes()
        (self.ssh/'config').write_text(f'Host pcj01\n HostName 192.0.2.10\n IdentityFile {key.as_posix()}\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'拒绝覆盖'):self.m.import_existing(['pcj01'])
        self.assertEqual((self.store.root/'keys/pcj01').read_bytes(),old)
    def test_scan_prefixes_not_exp_suffix_and_keys_without_config(self):
        source=Store(self.root/'source');key,_=source.key('web-prod')
        (self.ssh/'web-standby').write_bytes(key.read_bytes())
        (self.ssh/'config').write_text(''.join(f'Host {alias}\n HostName 192.0.2.30\n User ccpc00\n IdentityFile {key.as_posix()}\n' for alias in ['web01','web-prod','web-exp','pcj-exp','pcj-backup']),encoding='utf-8')
        rows=self.m.inventory();self.assertEqual({r['keyName'] for r in rows},{'web01','web-prod','web-exp','pcj-exp','pcj-backup','web-standby'})
        orphan=next(r for r in rows if r['keyName']=='web-standby');self.assertEqual(orphan['host'],'')
        with self.assertRaises(ValueError):self.m.import_existing(['web-standby'])
        self.m.import_existing(['web-standby'],[dict(keyName='web-standby',host='192.0.2.88',user='operator',sshPort=2222)])
        self.assertEqual(self.m.profiles()[0]['host'],'192.0.2.88')
    def test_invalid_records_and_arbitrary_import_path_rejected(self):
        for field,value in [('keyName','../escape'),('host','x;id'),('user','root;id')]:
            p=profile();p[field]=value
            with self.assertRaises(ValueError):self.m.save([p])
        with self.assertRaises(ValueError): self.m.import_existing(['../id_rsa'])
    def test_real_password_enrollment_then_private_key_only_no_deployment(self):
        fixture=Fixture(self.root/'fixture')
        try:
            p=profile(host='127.0.0.1',user='test');p.update(sshPort=fixture.port,password='fixture-pass')
            self.m.save([p]);self.m.start('authorize',['pcj01'])
            self.assertTrue(wait(self.m)['results']['pcj01']['ok'],self.m.status())
            # Native OpenSSH must use the generated alias, key and enrolled host fingerprint.
            native = subprocess.run(['ssh', '-F', str(self.ssh/'config'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'pcj01', 'hostname'], capture_output=True, text=True, timeout=10)
            self.assertEqual(native.returncode, 0, native.stderr)
            self.assertEqual(native.stdout.strip(), 'OK')
            # Wrong password is deliberate: key-only test must not need it.
            p['password']='deliberately-wrong';self.m.save([p]);self.m.start('test',['pcj01'])
            self.assertTrue(wait(self.m)['results']['pcj01']['ok'],self.m.status())
            self.assertEqual(fixture.jobs,[])
            self.assertNotIn('deliberately-wrong','\n'.join(self.m.status()['logs']))
        finally:fixture.close()
    def test_existing_windows_host_fingerprint_is_honored(self):
        fixture=Fixture(self.root/'fixture')
        try:
            known=self.root/'.ssh';known.mkdir();path=known/'known_hosts'
            hostkeys=paramiko.HostKeys();hostkeys.add(f'[127.0.0.1]:{fixture.port}',fixture.hostkey.get_name(),fixture.hostkey);hostkeys.save(str(path))
            p=profile(host='127.0.0.1',user='test');p.update(sshPort=fixture.port,password='fixture-pass');self.m.save([p])
            with patch('transport.Path.home',return_value=self.root):
                self.m.start('authorize',['pcj01']);self.assertTrue(wait(self.m)['results']['pcj01']['ok'],self.m.status())
                # Stale trusted fingerprint must not be silently replaced by first-use enrollment.
                wrong=paramiko.RSAKey.generate(2048);hostkeys.add(f'[127.0.0.1]:{fixture.port}',wrong.get_name(),wrong);hostkeys.save(str(path));original=path.read_bytes()
                self.m.start('test',['pcj01']);self.assertFalse(wait(self.m)['results']['pcj01']['ok'])
                self.assertEqual(path.read_bytes(),original)
        finally:fixture.close()

    def test_password_test_never_generates_installs_or_uses_key(self):
        fixture=Fixture(self.root/'password-fixture')
        try:
            p=profile(host='127.0.0.1',user='test');p.update(sshPort=fixture.port,password='fixture-pass')
            self.m.save([p]);self.m.start('testPassword',['pcj01'])
            self.assertTrue(wait(self.m)['results']['pcj01']['ok'])
            self.assertFalse((self.store.root/'keys/pcj01').exists())
            self.assertFalse(fixture.keys)
            self.assertFalse((self.ssh/'config').exists())
            self.m.start('authorize',['pcj01']);self.assertTrue(wait(self.m)['results']['pcj01']['ok'])
            p['password']='wrong-password';self.m.save([p]);self.m.start('testPassword',['pcj01'])
            result=wait(self.m)['results']['pcj01']
            self.assertFalse(result['ok']);self.assertIn('本次未使用密钥',result['status'])
            self.m.start('test',['pcj01']);self.assertTrue(wait(self.m)['results']['pcj01']['ok'])
        finally:fixture.close()

    def test_unenrolled_key_failure_explains_password_was_not_tested(self):
        fixture=Fixture(self.root/'unregistered-fixture')
        try:
            p=profile(host='127.0.0.1',user='test');p.update(sshPort=fixture.port,password='fixture-pass')
            self.m.save([p]);self.store.key('pcj01');self.m.start('test',['pcj01'])
            result=wait(self.m)['results']['pcj01']
            self.assertFalse(result['ok']);self.assertIn('本次未使用密码',result['status'])
            self.assertFalse(fixture.keys)
        finally:fixture.close()

if __name__=='__main__': unittest.main()
