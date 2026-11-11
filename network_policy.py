"""Per-operation IPv4 egress selection; never changes system routes or metrics."""
import ipaddress
import json
import os
import socket
import struct
import subprocess

SNAPSHOT = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
@{
 adapters=@(Get-NetAdapter | Select-Object Name,ifIndex,Status,HardwareInterface,Virtual,InterfaceType)
 addresses=@(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,IPAddress,PrefixLength,AddressState,SkipAsSource)
 routes=@(Get-NetRoute -AddressFamily IPv4 | Select-Object InterfaceIndex,DestinationPrefix,NextHop,RouteMetric)
 interfaces=@(Get-NetIPInterface -AddressFamily IPv4 | Select-Object InterfaceIndex,InterfaceMetric)
} | ConvertTo-Json -Depth 4 -Compress
'''


def snapshot():
    if os.name != 'nt': return {}
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', SNAPSHOT],
                                capture_output=True, timeout=25, creationflags=0x08000000)
        if result.returncode: raise ValueError('PowerShell network query failed')
        return json.loads(result.stdout.decode('utf-8-sig'))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Do not silently claim wired selection when discovery failed.
        raise RuntimeError('无法读取 Windows 网卡和路由；请检查网络服务后重试') from exc


def select(data, target):
    ip = ipaddress.IPv4Address(target)
    if ip.is_loopback or not data: return None
    adapters = {a['ifIndex']: a for a in data.get('adapters', []) if a['Status'] == 'Up'}
    metrics = {a['InterfaceIndex']: a['InterfaceMetric'] for a in data.get('interfaces', [])}
    choices = []
    for route in data.get('routes', []):
        idx = route['InterfaceIndex']; adapter = adapters.get(idx)
        network = ipaddress.IPv4Network(route['DestinationPrefix'], strict=False)
        if not adapter or ip not in network: continue
        for addr in data.get('addresses', []):
            if addr['InterfaceIndex'] != idx or addr['SkipAsSource'] or addr['AddressState'] not in (4, 'Preferred'): continue
            source = ipaddress.IPv4Address(addr['IPAddress'])
            if source.is_loopback or source.is_link_local: continue
            wired = bool(adapter['HardwareInterface'] and not adapter['Virtual'] and adapter['InterfaceType'] == 6)
            subnet = ipaddress.IPv4Network(f"{source}/{addr['PrefixLength']}", strict=False)
            # Preserve more-specific LAN/VPN routes. Prefer physical wired at equal specificity,
            # even when Windows gives Wi-Fi a lower metric. Then choose its matching source subnet.
            key = (-network.prefixlen, not wired, (route['RouteMetric'] or 0) + (metrics.get(idx) or 0),
                   ip not in subnet, -addr['PrefixLength'], idx, int(source))
            choice = dict(interface=idx, source=str(source), name=adapter['Name'], wired=wired,
                          route=str(network), nextHop=route['NextHop'])
            choices.append((key, choice))
    return min(choices, key=lambda c: c[0])[1] if choices else None


def describe(choice):
    if not choice: return '系统默认路由（无可绑定的活动 IPv4 接口）'
    return ('有线优先' if choice['wired'] else '按目标路由回退') + '：' + choice['name'] + ' / ' + choice['source'] + ' / 路由 ' + choice['route']


def bind(connection, choice):
    if not choice: return
    if os.name == 'nt':
        connection.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', choice['interface']))  # IP_UNICAST_IF
    connection.bind((choice['source'], 0))


def connect(target, port, choice, timeout=12):
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout); bind(connection, choice)
        connection.connect((target, port))
        return connection
    except BaseException:
        connection.close(); raise


def echo(target, choice, timeout=600):
    """Windows IPv4 ICMP with an explicit source (ping -S is IPv6-only)."""
    import ctypes as c
    from ctypes import wintypes as w
    api = c.WinDLL('iphlpapi', use_last_error=True)
    api.IcmpCreateFile.restype = w.HANDLE
    api.IcmpCloseHandle.argtypes = [w.HANDLE]
    api.IcmpSendEcho2Ex.argtypes = [w.HANDLE, w.HANDLE, c.c_void_p, c.c_void_p, w.ULONG, w.ULONG,
                                  c.c_void_p, w.WORD, c.c_void_p, c.c_void_p, w.DWORD, w.DWORD]
    api.IcmpSendEcho2Ex.restype = w.DWORD
    handle = api.IcmpCreateFile()
    if handle == c.c_void_p(-1).value: raise OSError('IcmpCreateFile failed')
    try:
        reply = c.create_string_buffer(512); payload = c.create_string_buffer(b'DT')
        address = lambda value: struct.unpack('=I', socket.inet_aton(value))[0]
        count = api.IcmpSendEcho2Ex(handle, None, None, None, address(choice['source']), address(target),
                                   payload, 2, None, reply, len(reply), timeout)
        # ICMP_ECHO_REPLY begins with IPv4 address and DWORD status; only IP_SUCCESS counts.
        return bool(count and struct.unpack_from('=I', reply.raw, 4)[0] == 0)
    finally:
        api.IcmpCloseHandle(handle)
