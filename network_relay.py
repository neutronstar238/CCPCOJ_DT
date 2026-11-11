"""Windows socket over WSL interop stdio, keeping TLS/SSH end-to-end."""
import ipaddress
import os
import sys
import threading
from network_policy import connect


def main(args):
    target, port, source, interface = args
    target = str(ipaddress.IPv4Address(target)); port = int(port); interface = int(interface)
    if not 1 <= port <= 65535 or not 0 <= interface < 2**24: raise ValueError('Invalid relay endpoint')
    choice = dict(source=str(ipaddress.IPv4Address(source)), interface=interface) if source else None
    if os.name == 'nt':
        import msvcrt
        for stream in (sys.stdin, sys.stdout): msvcrt.setmode(stream.fileno(), os.O_BINARY)
    with connect(target, port, choice) as connection:
        connection.settimeout(None)
        def upload():
            try:
                while block := os.read(sys.stdin.fileno(), 65536): connection.sendall(block)
                connection.shutdown(1)
            except OSError: pass
        threading.Thread(target=upload, daemon=True).start()
        while block := connection.recv(65536):
            sys.stdout.buffer.write(block); sys.stdout.buffer.flush()


# Executed by the WSL controller. Only loopback listens, with an exact per-job endpoint allowlist.
# Windows creates the outbound socket, so neither WSL NAT nor its virtual NIC chooses the egress.
WSL_RELAY = r'''
import http.server, socket, threading, shlex
class EgressProxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_CONNECT(self):
        command = self.server.routes.get(self.path)
        if command is None:
            self.send_error(403); return
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with self.server.children_lock: self.server.children.add(process)
        try:
            self.send_response(200); self.end_headers(); self.wfile.flush()
            def download():
                try:
                    while block := os.read(process.stdout.fileno(), 65536): self.connection.sendall(block)
                except OSError: pass
                finally:
                    try: self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
            reader = threading.Thread(target=download, daemon=True); reader.start()
            while block := self.connection.recv(65536):
                process.stdin.write(block); process.stdin.flush()
        except OSError: pass
        finally:
            if process.poll() is None: process.terminate()
            process.wait()
            process.stdin.close(); process.stdout.close()
            with self.server.children_lock: self.server.children.discard(process)
        self.close_connection = True

class EgressServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, routes):
        super().__init__(('127.0.0.1', 0), EgressProxy)
        self.routes = routes; self.children = set(); self.children_lock = threading.Lock()
        threading.Thread(target=self.serve_forever, daemon=True).start()
    def stop(self):
        self.shutdown(); self.server_close()
        with self.children_lock:
            for child in self.children:
                if child.poll() is None: child.terminate()
                child.wait()

def setup_egress(data):
    routes = data.get('egress', {})
    if not routes: return None
    proxy = EgressServer(routes)
    for host in data['inventory']['all']['hosts'].values():
        endpoint = host['ansible_host'] + ':' + str(host['ansible_port'])
        if host['ansible_connection'] == 'paramiko':
            host['ansible_paramiko_proxy_command'] = shlex.join(routes[endpoint])
        elif host['ansible_connection'] == 'ssh':
            host['ansible_ssh_common_args'] = '-o ' + shlex.quote('ProxyCommand=' + shlex.join(routes[endpoint]))
        else:
            host['ansible_winrm_proxy'] = 'http://127.0.0.1:' + str(proxy.server_port)
    return proxy
'''

if __name__ == '__main__': main(sys.argv[1:])
