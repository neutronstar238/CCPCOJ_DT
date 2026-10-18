import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oj import Client

class LoginSessionTests(unittest.TestCase):
    def test_password_is_verified_again_after_initialization_or_previous_login(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                data=self.rfile.read(int(self.headers['Content-Length']));seen.append(data)
                obj={'code':0,'msg':'already logged in'} if self.headers.get('Cookie') else {'code':1,'data':{}}
                raw=json.dumps(obj).encode();self.send_response(200);self.send_header('Set-Cookie','session=fixture; Path=/');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            c=Client(dict(host='127.0.0.1',port=server.server_port,adminUser='admin',secrets={'admin':'fixture-password'}))
            c.api('/initialize',{'x':1})
            c.login();c.login()
            self.assertEqual(len(seen),3);self.assertIn(b'password=fixture-password',seen[-1])
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
