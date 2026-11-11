"""Explicit first-use SSH trust for the Windows-to-WSL deployment controller."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import paramiko
import network_policy as network
from storage import restrict


def host_name(node):
    return node['host'] if node['port'] == 22 else f"[{node['host']}]:{node['port']}"


def known_sources(root):
    return [Path.home() / '.ssh' / 'known_hosts', root / 'known_hosts', root / 'arena_known_hosts']


def check_key(root, name, key):
    found = False
    for path in known_sources(root):
        if not path.exists(): continue
        keys = paramiko.HostKeys(str(path))
        entry = keys.lookup(name)
        if entry is None: continue
        if key.get_name() not in entry or entry[key.get_name()] != key:
            raise ValueError(f'{name}：SSH 主机指纹与已保存记录不一致，已停止；请核对服务器身份，原记录保留')
        found = True
    return found


def inspect_hosts(root, nodes, snapshot):
    """Only SSH handshake; do not authenticate or run remote commands."""
    nodes = [n for n in nodes if n['os'] == 'linux' or n.get('privateKeyPath')]
    def inspect(node):
        name = host_name(node)
        route = network.select(snapshot, node['host'])
        with network.connect(node['host'], node['port'], route) as sock:
            with paramiko.Transport(sock) as transport:
                transport.start_client(timeout=15)
                key = transport.get_remote_server_key()
        trusted = check_key(root, name, key)
        return dict(host=name, algorithm=key.get_name(), fingerprint=key.fingerprint,
                    publicKey=key.get_base64(), trusted=trusted)
    with ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(inspect, nodes))


def key_from_record(record):
    import base64
    return paramiko.PKey.from_type_string(record['algorithm'], base64.b64decode(record['publicKey'], validate=True))


def accept_hosts(root, records):
    path = root / 'arena_known_hosts'
    keys = paramiko.HostKeys(str(path)) if path.exists() else paramiko.HostKeys()
    for record in records:
        key = key_from_record(record)
        check_key(root, record['host'], key)  # Never replace a conflicting pin.
        keys.add(record['host'], key.get_name(), key)
    temp = path.with_suffix('.tmp')
    keys.save(str(temp)); restrict(temp); temp.replace(path)


def known_hosts_text(records):
    return ''.join(f"{r['host']} {r['algorithm']} {r['publicKey']}\n" for r in records)
