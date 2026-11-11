"""Real Windows/WSL TCP + SSH + TLS proxy checks. Local listeners only, no venue hosts."""
import datetime
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from arena_deploy import wsl_path, CONTROLLER
from network_relay import WSL_RELAY
from ssh_auth import prepare_keys, load_key
from ssh_check import Fixture
import network_policy as network


CHECK = r'''
import json, os, subprocess, sys, shlex, paramiko, winrm
data=json.load(sys.stdin)
'''+WSL_RELAY+r'''
command=data['ssh']
with paramiko.ProxyCommand(shlex.join(command)) as tunnel:
    client=paramiko.SSHClient()
    client.load_host_keys(data['known'])
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect('127.0.0.1',port=data['sshPort'],username='test',password='fixture-pass',sock=tunnel,
                   allow_agent=False,look_for_keys=False,timeout=15,banner_timeout=15)
    _,out,_=client.exec_command('hostname');assert out.read().strip()==b'OK'
    client.close()
print('WSL -> Windows stdio -> trusted SSH OK')
import tempfile, pathlib, shutil
with tempfile.TemporaryDirectory(prefix='dt-egress-fixture-') as home:
    sshdir=pathlib.Path(home)/'.ssh'; sshdir.mkdir()
    shutil.copyfile(data['known'],sshdir/'known_hosts')
    job=dict(egress={'127.0.0.1:'+str(data['sshPort']):command},
             inventory={'all':{'hosts':{'fixture':dict(ansible_host='127.0.0.1',ansible_port=data['sshPort'],
                        ansible_user='test',ansible_password='fixture-pass',ansible_connection='paramiko')}}},
             playbook=[dict(hosts='fixture',gather_facts=False,tasks=[{'ansible.builtin.raw':'hostname','changed_when':False}])])
    env=dict(os.environ,HOME=home,PATH=os.path.dirname(sys.executable)+os.pathsep+os.environ['PATH'],ANSIBLE_PARAMIKO_PTY='False')
    result=subprocess.run([sys.executable,'-c',data['controller']],input=json.dumps(job),text=True,capture_output=True,env=env,timeout=30)
    assert result.returncode==0 and 'unreachable=0' in result.stdout, result.stdout+result.stderr
    print('Actual WSL Ansible controller -> Windows connector -> SSH raw task OK')
    keyjob=data['keyJob'];keyjob['egress']=job['egress']
    jobtemp=pathlib.Path(home)/'jobs';jobtemp.mkdir();env['TMPDIR']=str(jobtemp)
    keyjob['inventory']['all']['hosts']['fixture']['ansible_ssh_extra_args']+=' -o '+shlex.quote('UserKnownHostsFile='+data['known'])
    for connection in ('paramiko','ssh'):
        keyjob['inventory']['all']['hosts']['fixture']['ansible_connection']=connection
        result=subprocess.run([sys.executable,'-c',data['controller']],input=json.dumps(keyjob),text=True,capture_output=True,env=env,timeout=30)
        assert result.returncode==0 and 'failed=0' in result.stdout, result.stdout+result.stderr
        assert data['unlock'] not in result.stdout+result.stderr
        assert not list(jobtemp.iterdir()), 'private key temporary directory leaked'
    keyjob['playbook'][0]['tasks']=[{'ansible.builtin.fail':{'msg':'intentional cleanup test'}}]
    result=subprocess.run([sys.executable,'-c',data['controller']],input=json.dumps(keyjob),text=True,capture_output=True,env=env,timeout=30)
    assert result.returncode!=0 and not list(jobtemp.iterdir())
    print('Encrypted external key -> private WSL key -> Ansible Paramiko and OpenSSH authentication OK')
proxy=EgressServer({data['tlsEndpoint']:data['tls']})
try:
    from winrm.transport import Transport
    transport=Transport(endpoint='https://'+data['tlsEndpoint'],username='fixture',password='fixture',
                        auth_method='ntlm',server_cert_validation='validate',ca_trust_path=data['cert'],
                        proxy='http://127.0.0.1:'+str(proxy.server_port))
    response=transport.build_session().get('https://'+data['tlsEndpoint'],timeout=15)
    assert response.content==bytes(range(256))*512
    transport.close_session()
    print('WSL pywinrm -> CONNECT -> Windows -> verified TLS / 128 KiB binary OK')
finally:proxy.stop()
'''


def main():
    root=Path(__file__).resolve().parents[1]
    launcher=[wsl_path(sys.argv[1])] if len(sys.argv)>1 else [wsl_path(sys.executable),str(root/'server.py')]
    launcher+=['--network-connect']
    # Verify real Windows API with the currently assigned NIC address; no literal LAN IP.
    snap=network.snapshot()
    active={a['ifIndex'] for a in snap['adapters'] if a['Status']=='Up' and a['HardwareInterface']}
    addr=next(a['IPAddress'] for a in snap['addresses'] if a['InterfaceIndex'] in active and a['AddressState']==4 and not a['SkipAsSource'])
    route=network.select(snap,addr)
    with socket.socket() as listener:
        listener.bind((addr,0));listener.listen()
        with network.connect(addr,listener.getsockname()[1],route) as client:
            peer,_=listener.accept()
            with peer:assert client.getsockname()[0]==route['source']
    assert network.echo(addr,route), 'source-bound ICMP failed'
    print('Live NIC snapshot, source/interface-bound TCP and IPv4 ICMP OK (self only)')
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp);fixture=Fixture(path/'ssh')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload=bytes(range(256))*512
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DT local fixture')]);now=datetime.datetime.now(datetime.timezone.utc)
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256())
        (path/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (path/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(path/'cert.pem',path/'key.pem')
        server.socket=context.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        import paramiko
        known=paramiko.HostKeys();known.add(f'[127.0.0.1]:{fixture.port}',fixture.hostkey.get_name(),fixture.hostkey);known.save(str(path/'known_hosts'))
        data=dict(controller=CONTROLLER,ssh=launcher+['127.0.0.1',str(fixture.port),'','0'],sshPort=fixture.port,known=wsl_path(path/'known_hosts'),
                  tls=launcher+['127.0.0.1',str(server.server_port),'','0'],tlsEndpoint=f'127.0.0.1:{server.server_port}',cert=wsl_path(path/'cert.pem'))
        unlock=' fixture key unlock ';keyfile=path/'encrypted key'
        keyfile.write_bytes(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.BestAvailableEncryption(unlock.encode())))
        fixture.keys.add(load_key(dict(privateKeyPath=str(keyfile),keyPassphrase=unlock)).get_base64())
        keyjob=dict(inventory={'all':{'hosts':{'fixture':dict(ansible_host='127.0.0.1',ansible_port=fixture.port,ansible_user='test',ansible_connection='paramiko',ansible_paramiko_look_for_keys=False,ansible_ssh_extra_args='-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes',_dt_key_path=str(keyfile),_dt_key_passphrase=unlock)}}},
                    playbook=[dict(hosts='fixture',gather_facts=False,tasks=[{'ansible.builtin.raw':'hostname','changed_when':False}])])
        prepare_keys(keyjob);data.update(keyJob=keyjob,unlock=unlock)
        try:
            bootstrap='import os; p=os.path.expanduser("~/.local/share/csgoj-ansible/venv/bin/python"); os.execv(p,[p,"-c",'+repr(CHECK)+'])'
            result=subprocess.run(['wsl.exe','-d','Ubuntu-24.04','--','python3','-c',bootstrap],input=json.dumps(data),text=True,capture_output=True,timeout=60)
            print(result.stdout)
            assert result.returncode==0, result.stderr
            assert 'trusted SSH OK' in result.stdout and '128 KiB binary OK' in result.stdout, result.stderr
        finally:
            fixture.close();server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
