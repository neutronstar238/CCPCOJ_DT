import copy
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch
import network_policy as network
from network_relay import WSL_RELAY


def fixture():
    return dict(adapters=[dict(Name='USB 有线', ifIndex=8, Status='Up', HardwareInterface=True, Virtual=False, InterfaceType=6),
                          dict(Name='无线', ifIndex=9, Status='Up', HardwareInterface=True, Virtual=False, InterfaceType=71)],
                interfaces=[dict(InterfaceIndex=8, InterfaceMetric=50),dict(InterfaceIndex=9, InterfaceMetric=1)],
                addresses=[dict(InterfaceIndex=i,IPAddress=ip,PrefixLength=24,AddressState=4,SkipAsSource=False)
                           for i,ip in [(8,'192.0.2.10'),(9,'192.0.2.20')]],
                routes=[dict(InterfaceIndex=i,DestinationPrefix='192.0.2.0/24',NextHop='0.0.0.0',RouteMetric=0) for i in (8,9)])


class NetworkTests(unittest.TestCase):
    def test_wired_beats_wifi_metric_and_names_do_not_classify(self):
        data=fixture();data['adapters'][0]['Name']='anything'
        route=network.select(data,'192.0.2.55')
        self.assertEqual(route['interface'],8);self.assertTrue(route['wired'])

    def test_dhcp_refresh_disconnected_and_virtual_fallback(self):
        data=fixture();data['addresses'][0]['IPAddress']='192.0.2.33'
        self.assertEqual(network.select(data,'192.0.2.55')['source'],'192.0.2.33')
        data['adapters'][0]['Status']='Disconnected'
        self.assertEqual(network.select(data,'192.0.2.55')['interface'],9)
        data['adapters'][0]['Status']='Up';data['adapters'][0]['Virtual']=True
        self.assertEqual(network.select(data,'192.0.2.55')['interface'],9)

    def test_more_specific_route_preserved_and_multisubnet_source(self):
        data=fixture();data['routes'][0]['DestinationPrefix']='0.0.0.0/0'
        self.assertEqual(network.select(data,'192.0.2.55')['interface'],9)
        data=fixture();data['addresses'].insert(0,dict(data['addresses'][0],IPAddress='198.51.100.2'))
        self.assertEqual(network.select(data,'192.0.2.55')['source'],'192.0.2.10')
        data['addresses'][1]['SkipAsSource']=True
        self.assertEqual(network.select(data,'192.0.2.55')['source'],'198.51.100.2')
        self.assertIsNone(network.select(data,'127.0.0.1'))
        self.assertIsNone(network.select(data,'203.0.113.1'))

    def test_binding_is_network_order_and_source_failures_are_not_ignored(self):
        connection=Mock();route=network.select(fixture(),'192.0.2.55')
        with patch('network_policy.os.name','nt'): network.bind(connection,route)
        connection.setsockopt.assert_called_once_with(socket.IPPROTO_IP,31,struct.pack('!I',8))
        connection.bind.assert_called_once_with(('192.0.2.10',0))
        connection.bind.side_effect=OSError('removed NIC')
        with self.assertRaises(OSError): network.bind(connection,route)

    def test_discovery_failure_is_explicit(self):
        with patch('network_policy.os.name','nt'),patch('network_policy.subprocess.run',side_effect=OSError()):
            with self.assertRaisesRegex(RuntimeError,'无法读取'):network.snapshot()

    def test_scanner_binds_tcp_and_icmp_same_source(self):
        from arena_scan import probe_host
        route=network.select(fixture(),'192.0.2.55');connection=Mock()
        connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock(return_value=False)
        connection.connect_ex.return_value=10060
        with patch('arena_scan.socket.socket',return_value=connection),patch('arena_scan.network.echo',return_value=True) as echo:
            self.assertEqual(probe_host('192.0.2.55',22,5986,route)['evidence'],'ICMP 响应')
        echo.assert_called_once_with('192.0.2.55',route)
        self.assertEqual(connection.bind.call_count,2)

    def test_binary_stdio_connector_and_half_close(self):
        payload=bytes(range(256))*512
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
            def echo():
                connection,_=listener.accept()
                with connection:
                    chunks=[]
                    while block:=connection.recv(65536):chunks.append(block)
                    connection.sendall(b''.join(chunks))
            thread=threading.Thread(target=echo,daemon=True);thread.start()
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'server.py'),
                                   '--network-connect','127.0.0.1',str(port),'','0'],input=payload,capture_output=True,timeout=15)
            thread.join(3)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,payload)

    def test_wsl_inventory_keeps_original_host_and_certificate_checks(self):
        namespace=dict(subprocess=subprocess,os=os)
        exec(WSL_RELAY,namespace)
        data=dict(egress={'192.0.2.55:22':['/mnt/c/program with spaces/python.exe','x','--network-connect','192.0.2.55','22','','0'],
                          '192.0.2.56:5986':['unused']},inventory={'all':{'hosts':{
                              'ssh':dict(ansible_host='192.0.2.55',ansible_port=22,ansible_connection='paramiko'),
                              'win':dict(ansible_host='192.0.2.56',ansible_port=5986,ansible_connection='winrm',ansible_winrm_server_cert_validation='validate')}}})
        proxy=namespace['setup_egress'](data)
        try:
            self.assertEqual(data['inventory']['all']['hosts']['ssh']['ansible_host'],'192.0.2.55')
            self.assertIn("'/mnt/c/program with spaces/python.exe'",data['inventory']['all']['hosts']['ssh']['ansible_paramiko_proxy_command'])
            self.assertEqual(data['inventory']['all']['hosts']['win']['ansible_winrm_server_cert_validation'],'validate')
            with socket.create_connection(proxy.server_address) as client:
                client.sendall(b'CONNECT 192.0.2.99:22 HTTP/1.0\r\n\r\n')
                self.assertIn(b'403',client.recv(4096))
        finally:proxy.stop()

if __name__=='__main__':unittest.main()
