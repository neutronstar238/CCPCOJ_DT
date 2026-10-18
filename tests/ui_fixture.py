"""Loopback-only UI fixture. All task actions are simulated, never run SSH/Docker."""
import copy,json,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

WEB=Path(__file__).resolve().parents[1]/'web'
CONFIG=dict(nodes=[dict(id='qa-pcj',kind='judge',host='127.0.0.1',user='qa',sshPort=22,
    keyName='qa-pcj',role='active',path='/tmp/qa',pods=2,web='qa-web',targetVersion='',secrets={})],backups=[])
PROFILES=[dict(keyName='qa-pcj',host='127.0.0.1',user='qa',sshPort=22,password='',exists=False)]
EMPTY=dict(running=False,job=None,status='等待',logs=[],nodes={},results={})
STATES={key:copy.deepcopy(EMPTY) for key in ('state','ssh/state','artifacts/state','local/state')}
LONG='C:/qa/'+('long-directory-'*35)+'/images'
CACHE=dict(cacheDir=LONG,indexPath=LONG+'/index.json',selected={},dependencies=[],archives=[dict(
    id='fixture',name='qa.tar',path=LONG+'/qa.tar',sha256='a'*64,size=100,available=True,
    images={'csgrandeur/ccpcoj-web2:qa-version':'sha256:'+'b'*64})])

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        key=self.path.removeprefix('/api/')
        if key in STATES:return self.respond(STATES[key])
        if key=='config':return self.respond(dict(config=CONFIG,dataDir='隔离的内存测试配置'))
        if key=='ssh/profiles':return self.respond(dict(profiles=PROFILES))
        if key=='ssh/inventory':return self.respond(dict(profiles=[dict(PROFILES[0],exists=True,fingerprint='QA fingerprint',sourcePath='C:/qa/key')]))
        if key=='artifacts/cache':return self.respond(CACHE)
        name='index.html' if self.path=='/' else self.path.lstrip('/')
        if name not in {p.name for p in WEB.iterdir() if p.is_file()}:return self.respond({'error':'fixture route not implemented'},404)
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript; charset=utf-8');self.end_headers();self.wfile.write((WEB/name).read_bytes())
    def do_POST(self):
        global CONFIG,PROFILES
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        key=self.path.removeprefix('/api/')
        if key=='save':
            time.sleep(.7);CONFIG=body['config'];return self.respond(dict(config=CONFIG,plan=[]))
        if key=='preferences':CONFIG.update(body['settings'])
        elif key=='ssh/save':PROFILES=body['profiles'];return self.respond(dict(profiles=PROFILES))
        elif key=='ssh/import':pass
        elif key=='ssh/run':
            PROFILES[0].update(exists=True,localPath='C:/qa/key',fingerprint='SHA256:fixture')
            STATES['ssh/state']=dict(EMPTY,job=uuid.uuid4().hex,status='完成');return self.respond({'ok':True})
        elif key=='artifacts/run':
            action=body['action'];job=uuid.uuid4().hex
            result=dict(archives=CACHE['archives'],docker=dict(images=[],available=False,message='QA 未连接 Docker'),roots=[LONG],rejected=[])
            STATES['artifacts/state']=dict(EMPTY,job=job,action=action,status='完成',result=result)
            return self.respond(dict(job=job))
        else:return self.respond({'error':'fixture does not execute operations'},400)
        return self.respond(dict(ok=True))

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print('http://127.0.0.1:'+str(server.server_port)+'/#local-fixture',flush=True)
    server.serve_forever()
