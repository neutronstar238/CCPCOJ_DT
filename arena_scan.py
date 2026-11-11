"""Bounded LAN discovery, independent of credentials and installation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import errno
import ipaddress
import os
import re
import socket
import subprocess
import threading
import uuid
import network_policy as network


def scan_settings(raw):
    addresses = set()
    ranges = raw.get('ranges', [])
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 32:
        raise ValueError('请填写 1–32 组起始 IP 和结束 IP')
    for row in ranges:
        try:
            start, end = (ipaddress.IPv4Address(str(row[k]).strip()) for k in ('start', 'end'))
        except (ValueError, KeyError): raise ValueError('起始和结束地址必须是完整 IPv4 地址') from None
        if int(end) < int(start): raise ValueError('结束 IP 不能小于起始 IP')
        if int(end) - int(start) >= 4096: raise ValueError('每次最多扫描 4096 个地址')
        for value in range(int(start), int(end) + 1):
            ip = ipaddress.IPv4Address(value)
            if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_reserved:
                raise ValueError('请填写有效的局域网主机地址范围')
            # Matches the deployment editor's supported /24 host suffixes.
            if (value & 255) in (0, 255): continue
            addresses.add(str(ip))
        if len(addresses) > 4096: raise ValueError('每次最多扫描 4096 个地址')
    if not addresses: raise ValueError('范围内没有尾号 1–254 的主机地址')
    ports = {}
    for key, default in (('sshPort', 22), ('winrmPort', 5986)):
        value = raw.get(key, default)
        if value in ('', None): value = default
        if not re.fullmatch(r'\d+', str(value)) or not 1 <= int(value) <= 65535: raise ValueError('管理端口必须为 1–65535')
        ports[key] = int(value)
    return dict(addresses=sorted(addresses, key=ipaddress.IPv4Address), **ports)


def probe_host(ip, ssh_port, winrm_port, route=None):
    opened, refused = [], []
    for port in dict.fromkeys((ssh_port, winrm_port)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(.6)
                network.bind(connection, route)
                code = connection.connect_ex((ip, port))
            if code == 0: opened.append(port)
            elif code in (errno.ECONNREFUSED, 10061): refused.append(port)
        except OSError: pass
    ping = False
    # Successful connect or a TCP refusal already establishes a response.
    if not opened and not refused:
        command = ['ping.exe', '-n', '1', '-w', '600', ip] if os.name == 'nt' else ['ping', '-n', '-c', '1', '-W', '1', ip]
        try:
            if route and os.name == 'nt':
                ping = network.echo(ip, route)
            else:
                r = subprocess.run(command, capture_output=True, timeout=2, creationflags=0x08000000 if os.name == 'nt' else 0)
                # Windows may return code 0 for destination-unreachable. Require a TTL reply.
                ping = r.returncode == 0 and bool(re.search(rb'\bttl[=\s]', r.stdout.lower()))
        except (OSError, subprocess.TimeoutExpired): pass
    if not (opened or refused or ping): return None
    return dict(ip=ip, ports=opened, evidence='管理端口可连接' if opened else 'TCP 拒绝响应' if refused else 'ICMP 响应',
                suggestedOS='windows' if winrm_port in opened and ssh_port not in opened else 'linux',
                suggestedPort=winrm_port if winrm_port in opened and ssh_port not in opened else ssh_port)


class ArenaScanner:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.running = False
        self.state = dict(id=None, running=False, status='尚未扫描', total=0, completed=0, found=[], errors=0)

    def status(self):
        with self.lock: return copy.deepcopy(self.state)

    def start(self, raw):
        settings = scan_settings(raw)
        with self.lock:
            if self.running: raise ValueError('扫描正在进行')
            self.cancel.clear(); self.running = True
            self.state = dict(id=uuid.uuid4().hex, running=True, status='扫描中', total=len(settings['addresses']), completed=0, found=[], errors=0)
        threading.Thread(target=self._run, args=(settings,), daemon=True).start()
        return dict(ok=True, id=self.state['id'])

    def _run(self, settings):
        def check(ip):
            if self.cancel.is_set(): return False, None
            return True, probe_host(ip, settings['sshPort'], settings['winrmPort'], routes[ip])
        try:
            snapshot = network.snapshot()
            routes = {ip: network.select(snapshot, ip) for ip in settings['addresses']}
            with self.lock: self.state['network'] = list(dict.fromkeys(network.describe(r) for r in routes.values()))
            with ThreadPoolExecutor(max_workers=32) as pool:
                futures = [pool.submit(check, ip) for ip in settings['addresses']]
                for future in as_completed(futures):
                    try: attempted, result = future.result()
                    except Exception:
                        attempted, result = True, None
                        with self.lock: self.state['errors'] += 1
                    with self.lock:
                        if attempted: self.state['completed'] += 1
                        if result:
                            self.state['found'].append(result)
                            self.state['found'].sort(key=lambda x: ipaddress.IPv4Address(x['ip']))
            with self.lock: self.state['status'] = '已停止' if self.cancel.is_set() else '扫描完成'
        except Exception as exc:
            with self.lock: self.state['status'] = '扫描失败：' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        finally:
            with self.lock: self.running = False; self.state['running'] = False
