import errno
import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request
import urllib.error
from arena_scan import ArenaScanner, probe_host, scan_settings


class ScanTests(unittest.TestCase):
    def setUp(self):
        p = patch('arena_scan.network.snapshot', return_value={})
        p.start(); self.addCleanup(p.stop)

    def test_multiple_ranges_deduplicate_and_cross_subnet(self):
        s=scan_settings({'ranges':[{'start':'192.0.2.253','end':'192.0.3.2'},{'start':'192.0.3.1','end':'192.0.3.3'}]})
        self.assertEqual(s['addresses'],['192.0.2.253','192.0.2.254','192.0.3.1','192.0.3.2','192.0.3.3'])
        self.assertEqual(s['sshPort'],22);self.assertEqual(s['winrmPort'],5986)

    def test_invalid_or_excessive_ranges_rejected(self):
        for start,end in [('192.0.2.4','192.0.2.3'),('192.0.0.1','192.1.0.1'),('127.0.0.1','127.0.0.2'),('224.0.0.1','224.0.0.2'),('x;whoami','192.0.2.1')]:
            with self.subTest(start=start),self.assertRaises(ValueError):scan_settings({'ranges':[dict(start=start,end=end)]})
        with self.assertRaises(ValueError):scan_settings({'ranges':[dict(start='192.0.2.1',end='192.0.2.2')],'sshPort':0})

    def test_real_loopback_listener_discovered_without_login_or_ping(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
            with patch('arena_scan.subprocess.run') as ping:
                r=probe_host('127.0.0.1',port,port)
            self.assertEqual(r['ports'],[port]);ping.assert_not_called()

    def test_refused_tcp_is_response_but_unreachable_ping_is_not(self):
        connection=Mock();connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock(return_value=None)
        with patch('arena_scan.socket.socket',return_value=connection),patch('arena_scan.subprocess.run') as ping:
            connection.connect_ex.return_value=errno.ECONNREFUSED
            r=probe_host('192.0.2.1',22,5986)
            self.assertEqual(r['evidence'],'TCP 拒绝响应');self.assertEqual(r['ports'],[]);ping.assert_not_called()
            connection.connect_ex.return_value=errno.ETIMEDOUT
            ping.return_value=Mock(returncode=0,stdout=b'Destination host unreachable')
            self.assertIsNone(probe_host('192.0.2.1',22,5986))
            ping.return_value=Mock(returncode=0,stdout=b'Reply from 192.0.2.1: bytes=32 TTL=64')
            self.assertEqual(probe_host('192.0.2.1',22,5986)['evidence'],'ICMP 响应')

    def test_progress_counts_only_replies_and_cancellation_skips_work(self):
        scanner=ArenaScanner();s=scan_settings({'ranges':[dict(start='192.0.2.1',end='192.0.2.3')]})
        scanner.state['total']=3
        def probe(ip,*args):return dict(ip=ip,ports=[22],evidence='管理端口可连接',suggestedOS='linux',suggestedPort=22) if ip.endswith('.2') else None
        with patch('arena_scan.probe_host',side_effect=probe):scanner._run(s)
        self.assertEqual(scanner.status()['completed'],3);self.assertEqual(len(scanner.status()['found']),1)
        scanner=ArenaScanner();scanner.cancel.set()
        with patch('arena_scan.probe_host') as check:scanner._run(s)
        check.assert_not_called();self.assertEqual(scanner.status()['status'],'已停止');self.assertEqual(scanner.status()['completed'],0)

    def test_scan_api_authenticated_busy_and_cancel_available(self):
        from server import make_server
        from storage import Store
        entered,release=threading.Event(),threading.Event()
        def fake(*args):entered.set();release.wait(3);return None
        with tempfile.TemporaryDirectory() as tmp,patch('arena_scan.probe_host',side_effect=fake):
            server=make_server(Store(tmp));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(path,data=None,token=True):
                req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-CSG-Token':server.token if token else ''})
                with urllib.request.urlopen(req) as r:return json.load(r)
            try:
                with self.assertRaises(urllib.error.HTTPError):request('arena/scan/state',token=False)
                request('arena/scan/start',dict(ranges=[dict(start='192.0.2.1',end='192.0.2.1')]))
                self.assertTrue(entered.wait(1));self.assertTrue(request('state')['activity']['scan'])
                with self.assertRaises(urllib.error.HTTPError):request('arena/run',dict(role='client',action='deploy'))
                request('arena/scan/cancel',{});self.assertTrue(server.arena_scan.cancel.is_set())
            finally:
                release.set()
                deadline=time.monotonic()+3
                while server.arena_scan.running and time.monotonic()<deadline:time.sleep(.01)
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
