"""Loopback-only Windows deployment console, no public HTTP listener."""
import argparse
import base64
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import urllib.request
import urllib.error
from urllib.parse import unquote
import webbrowser
from engine import Engine
from models import default_config, normalize, plan, redact, protect_managed
from storage import Store
from xlsx_import import preview as xlsx_preview
from ssh_manager import SSHManager
from credential_export import export_text
from local_deploy import LocalDeployment, wsl_list

ROOT = Path(__file__).resolve().parent

from artifacts import download_tool, image_tags, ArtifactJobs

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def response(self, code, data, content='application/json; charset=utf-8', filename=None):
        if isinstance(data, (dict, list)): data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str): data = data.encode()
        self.send_response(code)
        self.send_header('Content-Type', content); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename: self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers(); self.wfile.write(data)
    def allowed(self):
        host = '127.0.0.1:' + str(self.server.server_port)
        if self.headers.get('Host') != host: self.response(403, dict(error='Host 被拒绝')); return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host: self.response(403, dict(error='Origin 被拒绝')); return False
        if self.path.startswith('/api/') and not secrets.compare_digest(self.headers.get('X-CSG-Token', ''), self.server.token):
            self.response(403, dict(error='本地会话无效，请重新启动工具')); return False
        return True
    def do_GET(self):
        if not self.allowed(): return
        try:
            if self.path == '/api/local/config': return self.response(200, self.server.local.describe())
            if self.path == '/api/local/state': return self.response(200, self.server.local.engine.status())
            if self.path == '/api/local/distros': return self.response(200, dict(distros=wsl_list()))
            if self.path == '/api/ssh/profiles': return self.response(200, dict(profiles=self.server.ssh_manager.describe()))
            if self.path == '/api/ssh/inventory': return self.response(200, dict(profiles=self.server.ssh_manager.inventory()))
            if self.path == '/api/ssh/state': return self.response(200, self.server.ssh_manager.status())
            if self.path == '/api/artifacts/state': return self.response(200, self.server.artifacts.status())
            if self.path == '/api/artifacts/cache':
                cache = self.server.artifacts.cache
                return self.response(200, cache.describe())
            if self.path == '/api/state':
                state=self.server.engine.status()
                state['activity']=dict(remote=self.server.engine.running,ssh=self.server.ssh_manager.running,artifacts=self.server.artifacts.running,local=self.server.local.running)
                return self.response(200,state)
            if self.path == '/api/config':
                return self.response(200, dict(config=self.server.store.load() or default_config(), dataDir=str(self.server.store.root)))
            if self.path == '/api/export':
                c = self.server.store.load() or default_config()
                exported = {k: v for k, v in c.items() if k != 'nodes'}
                exported['nodes'] = [{k: v for k, v in n.items() if k not in ('password', 'elevatePassword')} for n in c['nodes']]
                exported['notice'] = '包含数据库、管理员与评测机口令，请妥善保管；不包含 SSH 登录和提权密码。私钥另存于本机 keys 目录。'
                return self.response(200, exported, filename='csgoj-deployment-credentials.json')
            if self.path == '/api/log': return self.response(200, '\n'.join(self.server.engine.status()['logs']), 'text/plain; charset=utf-8', 'deployment.log')
            if self.path == '/api/template':
                return self.response(200, (ROOT / 'templates' / 'CSGOJ节点导入模板.xlsx').read_bytes(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'CSGOJ-nodes-template.xlsx')
            paths = {'/': 'index.html', '/app.js': 'app.js', '/ssh.js': 'ssh.js', '/artifacts.js': 'artifacts.js', '/batch.js': 'batch.js', '/archive-delete.js': 'archive-delete.js', '/database.js': 'database.js', '/local.js': 'local.js', '/tasks.js': 'tasks.js', '/style.css': 'style.css'}
            if self.path not in paths: return self.response(404, dict(error='未找到'))
            f = ROOT / 'web' / paths[self.path]
            self.response(200, f.read_bytes(), {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[f.suffix] + '; charset=utf-8')
        except Exception as e: self.response(500, dict(error=str(e)))
    def do_POST(self):
        with self.server.action_lock: self.handle_post()
    def invalidate_connections(self, previous):
        current = {n['id']:n for n in (self.server.store.load() or {}).get('nodes', [])}
        with self.server.engine.lock:
            for old in (previous or {}).get('nodes', []):
                new = current.get(old['id'], {})
                if any(old.get(k) != new.get(k) for k in ('host','user','sshPort','keyName')):
                    self.server.engine.state['nodes'].pop(old['id'], None)
    def handle_post(self):
        if not self.allowed(): return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 40 * 1024 * 1024: return self.response(413, dict(error='文件太大'))
            body = self.rfile.read(length)
            engine = self.server.engine; store = self.server.store
            if self.path == '/api/cancel':
                engine.cancel.set(); return self.response(200, dict(ok=True))
            if self.path == '/api/local/cancel':
                self.server.local.engine.cancel.set(); return self.response(200, dict(ok=True))
            if engine.running or self.server.local.running or self.server.ssh_manager.running or self.server.artifacts.running: raise ValueError('任务执行期间不能修改配置或启动另一个任务')
            if self.path == '/api/tool':
                result = store.tool(body, unquote(self.headers.get('X-CSG-Filename', '')))
                c = store.load() or default_config(); c['tool'] = result
                if result.get('version'): c['toolVersion'] = result['version']
                store.save(c)
                return self.response(200, result)
            data = json.loads(body or b'{}')
            if self.path == '/api/local/save': return self.response(200, dict(config=self.server.local.save_settings(data['settings'])))
            if self.path == '/api/local/run': return self.response(200, dict(job=self.server.local.start(data['action'], data.get('selected'), data.get('confirmed'))))
            manager = self.server.ssh_manager
            if self.path == '/api/preferences':
                c = store.load() or default_config()
                settings = data['settings']
                for key in ('disableGui', 'neverSleep', 'performance'):
                    if key in settings:
                        if not isinstance(settings[key], bool): raise ValueError('主机设置应为开关值')
                        c[key] = settings[key]
                for key in ('version', 'mirror'):
                    if key in settings: c[key] = settings[key]
                choices = settings.get('uiChoices', {})
                allowed = {'releaseVersion', 'imageVersion', 'updateVersion', 'imageKind', 'imageSource', 'imageDependencies'}
                if not isinstance(choices, dict) or set(choices) - allowed: raise ValueError('界面设置格式错误')
                c['uiChoices'] = choices
                c = normalize(c)
                store.save(c)
                return self.response(200, dict(ok=True))
            if self.path == '/api/artifacts/run': return self.response(200, dict(job=self.server.artifacts.start(data['action'], data)))
            if self.path == '/api/artifacts/select': return self.response(200, self.server.artifacts.cache.select(data['id']))
            if self.path == '/api/artifacts/delete': return self.response(200, self.server.artifacts.cache.delete(data['id'], data.get('confirmed')))
            if self.path == '/api/credentials-export':
                return self.response(200, export_text(store.load(), data.get('ids')), 'text/plain; charset=utf-8', 'csgoj-credentials.txt')
            if self.path in ('/api/ssh/save','/api/ssh/import'):
                before = store.load()
                profiles = manager.save(data['profiles']) if self.path.endswith('/save') else manager.import_existing(data['names'],data.get('connections'))
                self.invalidate_connections(before)
                return self.response(200, dict(profiles=profiles))
            if self.path == '/api/ssh/run':
                manager.start(data['action'], data['names']); return self.response(200, dict(ok=True))
            if self.path == '/api/xlsx-preview':
                candidate = xlsx_preview(base64.b64decode(data['file'], validate=True), data.get('config') or store.load() or default_config())
                protect_managed(store.load(), candidate['config'])
                return self.response(200, candidate)
            if self.path == '/api/tool-download':
                return self.response(200, download_tool(store, data['version']))
            if self.path == '/api/image-tags': return self.response(200,image_tags(data['kind']))
            if self.path == '/api/save':
                before = store.load()
                c = protect_managed(before, normalize(data['config'])); store.save(c); self.invalidate_connections(before)
                return self.response(200, dict(config=c, plan=plan(c)))
            if self.path == '/api/run':
                data['config'] = protect_managed(store.load(), normalize(data['config']))
                job = engine.start(data['action'], data['config'], data.get('selected'), data.get('source'))
                return self.response(200, dict(job=job))
            self.response(404, dict(error='未知接口'))
        except Exception as e:
            self.response(400, dict(error=redact(str(e), self.server.store.load() or {})))

def make_server(store=None, port=0):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.action_lock = threading.Lock()
    server.token = secrets.token_urlsafe(32); server.store = store or Store(); server.engine = Engine(server.store)
    server.ssh_manager = SSHManager(server.store); server.artifacts = ArtifactJobs(server.store)
    server.local = LocalDeployment(server.store)
    return server

def main():
    p = argparse.ArgumentParser(); p.add_argument('--no-browser', action='store_true'); p.add_argument('--port', type=int, default=0)
    p.add_argument('--data-dir'); args = p.parse_args()
    server = make_server(Store(args.data_dir) if args.data_dir else None, args.port)
    address = 'http://127.0.0.1:' + str(server.server_port) + '/#' + server.token
    print('CSGOJ 部署工作台；关闭此窗口将停止本地服务。执行任务期间请保持打开。', flush=True)
    print(address, flush=True)
    if not args.no_browser: threading.Timer(.5, lambda: webbrowser.open(address)).start()
    try: server.serve_forever()
    except KeyboardInterrupt:
        if server.engine.running:
            print('等待当前远程步骤结束；请勿在迁移中强制退出。', flush=True)
            server.engine.cancel.set()
        if server.local.running: server.local.engine.cancel.set()
        server.server_close()

if __name__ == '__main__': main()
