import http.cookiejar
from html.parser import HTMLParser
import json
import time
import urllib.parse
import urllib.request

def url(n): return 'http://' + n['host'] + ':' + str(n['port'])

class Client:
    def __init__(self, web):
        self.n, self.base = web, url(web)
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(data).encode() if data is not None else None,
            headers={'Origin': self.base, 'Referer': self.base + '/', 'X-Requested-With': 'XMLHttpRequest'})
        with self.op.open(req, timeout=20) as r: return r.read()
    def api(self, path, data=None):
        obj = json.loads(self.request(path, data))
        if isinstance(obj, dict) and obj.get('code') != 1: raise RuntimeError(path + '：' + str(obj.get('msg', '接口响应失败')))
        return obj.get('data') if isinstance(obj, dict) else obj
    def login(self):
        # Initialization and earlier checks may already have authenticated this cookie jar.
        # Verify the supplied password using a fresh session, not an "already logged in" response.
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.api('/cpcsys/user/login_ajax', dict(user_id=self.n['adminUser'], password=self.n['secrets']['admin']))
        return self
    def verify(self, initialize=False):
        last = None
        for _ in range(45):
            try: page = self.request('/').decode(); break
            except Exception as e: last = e; time.sleep(2)
        else: raise RuntimeError('站点 90 秒内未就绪：' + str(last))
        if 'init_user_form' in page:
            if not initialize: raise RuntimeError('迁移后站点变成初始化页面，数据未正确恢复')
            self.api('/ojtool/userinit/init_user_ajax', dict(user_id=self.n['adminUser'], password=self.n['secrets']['admin'], confirm_password=self.n['secrets']['admin']))
        self.login()
        html = self.request('/cpcsys/contest').decode()
        parser = Assets(); parser.feed(html)
        if not parser.refs: raise RuntimeError('页面未发现 CSS 样式资源')
        for ref in parser.refs:
            full = urllib.parse.urljoin(self.base + '/cpcsys/contest', ref)
            if urllib.parse.urlsplit(full).netloc != urllib.parse.urlsplit(self.base).netloc: continue
            with self.op.open(full, timeout=20) as r:
                if not r.read(100) or 'text/html' in r.headers.get('Content-Type', ''): raise RuntimeError('CSS 资源异常：' + ref)
        return dict(http=True, adminLogin=True, stylesheets=len(parser.refs))
    def judges(self):
        rows = self.api('/admin/judger/judger_list_ajax')
        if not isinstance(rows, list): raise RuntimeError('评测账号列表格式不兼容')
        return rows
    def heartbeat(self, judge, previous=None):
        for _ in range(25):
            row = next((r for r in self.judges() if r['user_id'] == judge['judgeUser']), None)
            if row and row.get('accesstime') and row.get('accesstime') != previous:
                return dict(user=judge['judgeUser'], heartbeat=row['accesstime'], ip=row.get('ip'))
            time.sleep(2)
        raise RuntimeError('容器启动后 50 秒未检测到新的 OJ 心跳；不可视为评测可用')

class Assets(HTMLParser):
    def __init__(self): super().__init__(); self.refs = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and a.get('rel') == 'stylesheet' and a.get('href'): self.refs.append(a['href'])
