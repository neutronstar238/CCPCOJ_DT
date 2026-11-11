import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import paramiko
from ssh_auth import normalize_auth, load_key, private_material, prepare_keys, login_defaults, save_login_defaults
from arena_deploy import ArenaDeployment, defaults, normalize, bundle, targets
from storage import Store
from transport import Remote
from ssh_manager import SSHManager
from ssh_check import Fixture
from test_arena import sample
from models import redact


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.key=Ed25519PrivateKey.generate();self.path=self.root/'external private key'
        self.phrase=' fixture unlock '
        self.path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.BestAvailableEncryption(self.phrase.encode())))
        self.record=dict(privateKeyPath=str(self.path),keyPassphrase=self.phrase)

    def test_encrypted_key_load_and_single_job_material(self):
        original=self.path.read_bytes();k=load_key(self.record)
        self.assertEqual(k.get_name(),'ssh-ed25519')
        c=sample();c['clients'][0].update(self.record,user='root')
        b=bundle(c,'client','status',{})
        self.assertNotIn(self.phrase,json.dumps(b['playbook']))
        prepare_keys(b)
        self.assertEqual(len(b['keys']),1)
        self.assertNotIn(self.phrase,json.dumps(b));self.assertNotIn('client-secret',json.dumps(b))
        for host in b['inventory']['all']['hosts'].values():
            self.assertFalse(host['ansible_become']);self.assertNotIn('ansible_password',host);self.assertEqual(host['_dt_key_ref'],'0')
        material=next(iter(b['keys'].values()))
        self.assertEqual(serialization.load_ssh_private_key(material.encode(),None).public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH),self.key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH))
        self.assertEqual(self.path.read_bytes(),original)

    def test_bad_path_passphrase_and_redaction(self):
        for path in ('relative-key','\\\\server\\key','C:\\key\ninvalid'):
            with self.assertRaises(ValueError):normalize_auth(dict(privateKeyPath=path))
        wrong=dict(self.record,keyPassphrase='wrong')
        with self.assertRaisesRegex(ValueError,'解锁口令'):load_key(wrong)
        with self.assertRaisesRegex(ValueError,'解锁口令'):private_material(wrong)
        self.assertNotIn(self.phrase,redact('unlock '+self.phrase,dict(nodes=[self.record])))

    def test_machine_default_is_separate_and_empty_on_fresh_install(self):
        store=Store(self.root/'state')
        self.assertEqual(login_defaults(store),dict(user='root',privateKeyPath='',keyPassphrase=''))
        save_login_defaults(store,str(self.path))
        self.assertEqual(login_defaults(store)['privateKeyPath'],str(self.path))
        self.assertNotIn(str(self.path).encode(),(store.root/'login-defaults.dpapi').read_bytes())
        self.assertEqual(login_defaults(Store(self.root/'other'))['privateKeyPath'],'')

    def test_windows_key_uses_openssh_not_winrm(self):
        c=sample();c['clients'][0].update(self.record,os='windows',port='')
        c=normalize(c);self.assertEqual(c['clients'][0]['port'],22)
        b=bundle(c,'client','status',{});v=next(iter(b['inventory']['all']['hosts'].values()))
        self.assertEqual(v['ansible_connection'],'ssh');self.assertEqual(v['ansible_shell_type'],'powershell');self.assertNotIn('ansible_password',v)

    def test_unbound_draft_saved_but_deploy_blocked(self):
        c=sample();c['clients'][0]['server']='';c['servers']=[]
        c=normalize(c);self.assertTrue(all(n['endpoint'] is None for n in targets(c,'client')))
        manager=ArenaDeployment(Store(self.root/'state'));manager.save(c)
        with self.assertRaisesRegex(ValueError,'选择管控服务端'):manager.start('client','deploy')
        self.assertFalse(manager.running)

    def test_external_key_native_login_no_password_fallback_and_vault(self):
        fixture=Fixture(self.root/'remote');self.addCleanup(fixture.close)
        fixture.keys.add(load_key(self.record).get_base64())
        store=Store(self.root/'state')
        manager=SSHManager(store,ssh_dir=self.root/'ssh')
        n=dict(self.record,id='venue',keyName='venue',host='127.0.0.1',user='root',sshPort=fixture.port,password='deliberately wrong')
        manager.save([n]);self.assertNotIn(self.phrase.encode(),(store.root/'ssh-connections.dpapi').read_bytes())
        self.assertIn(self.path.as_posix(),(self.root/'ssh/config').read_text())
        r=Remote(n,store,lambda line:None).connect()
        try:
            self.assertTrue(r.ssh.get_transport().is_authenticated())
            self.assertTrue(r.op('health',{})['online'])
            self.assertNotIn('privateKeyPath',fixture.jobs[-1]['node'])
            self.assertNotIn('keyPassphrase',fixture.jobs[-1]['node'])
        finally:r.close()
        # A valid password must never hide a rejected external key.
        fixture.keys.clear();n['password']='fixture-pass'
        with self.assertRaises(paramiko.AuthenticationException):Remote(n,store,lambda line:None).connect()
        self.assertFalse((store.root/'keys/venue').exists())

if __name__=='__main__':unittest.main()
