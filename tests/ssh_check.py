"""Real local SSH/SFTP transport test against a disposable Paramiko fixture.
No system sshd, remote machines, sudo privileges or real passwords are used.
"""
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import paramiko
from storage import Store
from transport import Remote
from test_console import config

class Files(paramiko.SFTPServerInterface):
    def __init__(self,server,*a,**kw):super().__init__(server,*a,**kw);self.root=server.fixture.root
    def path(self,p):
        result=(self.root/p.lstrip('/')).resolve()
        if not result.is_relative_to(self.root):raise OSError('escape')
        return result
    def canonicalize(self,p):return '/home/test' if p=='.' else p
    def stat(self,p):
        try:return paramiko.SFTPAttributes.from_stat(self.path(p).stat())
        except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 2)
    lstat=stat
    def mkdir(self,p,attr):self.path(p).mkdir();return paramiko.SFTP_OK
    def rmdir(self,p):self.path(p).rmdir();return paramiko.SFTP_OK
    def remove(self,p):self.path(p).unlink();return paramiko.SFTP_OK
    def chattr(self,p,attr):return paramiko.SFTP_OK
    def open(self,p,flags,attr):
        try:
            mode='r+b' if flags&os.O_RDWR else 'wb' if flags&os.O_WRONLY else 'rb'
            f=open(self.path(p),mode);h=paramiko.SFTPHandle(flags);h.readfile=f;h.writefile=f;return h
        except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 2)

class Auth(paramiko.ServerInterface):
    def __init__(self,fixture):self.fixture=fixture
    def get_allowed_auths(self,u):return 'password,publickey'
    def check_auth_password(self,u,p):return paramiko.AUTH_SUCCESSFUL if p=='fixture-pass' else paramiko.AUTH_FAILED
    def check_auth_publickey(self,u,key):return paramiko.AUTH_SUCCESSFUL if key.get_base64() in self.fixture.keys else paramiko.AUTH_FAILED
    def check_channel_request(self,kind,cid):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_exec_request(self,ch,cmd):
        def answer():
            command=cmd.decode()
            try:
                if '.ssh/authorized_keys' in command:
                    self.fixture.keys.update(re.findall(r'ssh-ed25519 ([A-Za-z0-9+/=]+)',command))
                    ch.send(b'key installed\n')
                elif 'python3 ' in command:
                    # Parse root-shell wrapping without executing a command.
                    text=shlex.split(command)[-1] if command.startswith('sudo ') else command
                    args=shlex.split(text);job=json.loads((self.fixture.root/args[-1].lstrip('/')).read_text())
                    assert 'password' not in job['node'] and 'elevatePassword' not in job['node']
                    assert 'nodes' not in job['config'], 'other host credentials were uploaded'
                    assert job['action'] in ('health','prepare')
                    self.fixture.jobs.append(job)
                    ch.send(b'CSGRESULT:{"online":true,"fixture":true,"prepared":true,"images":[]}\n')
                else:ch.send(b'OK\n')
                ch.send_exit_status(0)
            except Exception as e:ch.send(str(e).encode());ch.send_exit_status(1)
            finally:ch.close()
        threading.Thread(target=answer,daemon=True).start();return True

class Fixture:
    def __init__(self,root):
        self.root=Path(root);(self.root/'home/test').mkdir(parents=True);self.keys=set();self.jobs=[];self.sessions=[]
        self.hostkey=paramiko.RSAKey.generate(2048);self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen();self.port=self.sock.getsockname()[1];self.closed=False
        threading.Thread(target=self.accept,daemon=True).start()
    def accept(self):
        while not self.closed:
            try:s,_=self.sock.accept()
            except OSError:return
            t=paramiko.Transport(s);t.add_server_key(self.hostkey);t.set_subsystem_handler('sftp',paramiko.SFTPServer,Files);self.sessions.append(t)
            try:t.start_server(server=Auth(self))
            except Exception:t.close()
    def close(self):
        self.closed=True;self.sock.close()
        for t in self.sessions:t.close()

def main():
    with tempfile.TemporaryDirectory() as temp:
        fixture=Fixture(Path(temp)/'remote');store=Store(Path(temp)/'local');logs=[]
        try:
            c=config();n=c['nodes'][0];n.update(host='127.0.0.1',sshPort=fixture.port,user='test',password='fixture-pass',elevatePassword='sudo-fixture')
            r=Remote(n,store,logs.append);r.alias_dir=Path(temp)/'ssh';r.connect(bootstrap=True)
            try:result=r.op('health',c);assert result['online'] and result['fixture']
            finally:r.close()
            assert fixture.jobs and fixture.keys
            assert not list((fixture.root/'home/test').glob('.csgoj-job-*')), 'temporary credentials not removed'
            # Password-free reconnect uses only the generated private key.
            n['password']='';r=Remote(n,store,logs.append).connect();r.close()
            assert (store.root/'known_hosts').exists()
            # Deliberately change pinned host key; connection must fail before auth.
            bad=paramiko.HostKeys();bad.add('[127.0.0.1]:'+str(fixture.port),'ssh-rsa',paramiko.RSAKey.generate(2048));bad.save(str(store.root/'known_hosts'))
            try:Remote(n,store,logs.append).connect();raise AssertionError('host key change accepted')
            except paramiko.BadHostKeyException:pass
            print('PASS: real SSH password bootstrap, generated-key reconnect, pinned-host rejection, SFTP upload/cleanup, minimal remote secrets payload.')
        finally:fixture.close()

if __name__=='__main__':main()
