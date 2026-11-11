"""Real browser/API workflow; deterministic probes, no LAN scan or installation."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from test_arena import sample
from test_network_policy import fixture as network_fixture


def main():
    hold=threading.Event()
    def probe(ip,*args):
        hold.wait(5)
        if ip.endswith('.41'):return None
        return dict(ip=ip,ports=[22],evidence='管理端口可连接',suggestedOS='linux',suggestedPort=22)
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp,patch('arena_scan.probe_host',side_effect=probe),patch('arena_scan.network.snapshot',side_effect=network_fixture):
        server=make_server(Store(temp));config=sample();config['clients']=[];server.arena.save(config)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
                page=browser.new_page(viewport={'width':1520,'height':1100})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                page.locator('[data-tab="arenaClient"]').click()
                expect(page.locator('#arenaScanServer')).to_have_value('s1')
                page.locator('#arenaScanRanges [data-start]').fill('192.0.2.40')
                page.locator('#arenaScanRanges [data-end]').fill('192.0.2.42')
                page.locator('#arenaScanStart').click()
                expect(page.locator('#arenaScanStop')).to_be_enabled()
                expect(page.locator('#arenaClient [data-action="deploy"]')).to_have_attribute('data-task-lock','')
                page.locator('#arenaScanStop').click();hold.set()
                expect(page.locator('#arenaScanState')).to_contain_text('已停止')
                page.locator('#arenaScanStart').click()
                expect(page.locator('#arenaScanState')).to_contain_text('扫描完成')
                expect(page.locator('#arenaScanResults tr')).to_have_count(2)
                expect(page.locator('#arenaScanState')).to_contain_text('在线响应 2 台')
                expect(page.locator('#arenaScanNetwork')).to_contain_text('有线优先：USB 有线 / 192.0.2.10')
                page.locator('#arenaScanUser').fill('contest')
                page.locator('#arenaScanPassword').fill('fixture-login')
                page.locator('#arenaScanApply').click()
                page.locator('[data-ip="192.0.2.42"] [data-seat="rootPassword"]').fill('fixture-sudo')
                assert page.locator('#arenaScanResults').evaluate("e=>[...e.querySelectorAll('input,select')].every(i=>{const a=i.getBoundingClientRect(),b=i.closest('td').getBoundingClientRect();return a.left>=b.left&&a.right<=b.right+1})"),'Result controls overflow their columns'
                page.evaluate("document.querySelector('#dataDir').textContent='独立测试资料目录';document.querySelectorAll('.arena-cache-path').forEach(e=>e.textContent='独立测试资料目录 / arena-cache')")
                page.screenshot(path=str(root/'verification/arena-scan.png'),full_page=False)
                page.locator('#arenaScanImport').click()
                expect(page.locator('#arenaClientRows .arena-row')).to_have_count(2)
                page.locator('#arenaClient [data-action="save"]').click()
                expect(page.locator('#arenaClientNotice')).to_contain_text('已加密保存')
                saved=server.arena.config()['clients']
                self_ips=[n['start'] for n in saved]
                assert self_ips==[40,42],self_ips
                assert saved[0]['password']=='fixture-login' and saved[0]['rootPassword']==''
                assert saved[1]['rootPassword']=='fixture-sudo'
                assert all(n['server']=='s1' for n in saved)
                assert server.arena.status()['status']=='尚未执行'
                page.reload();page.locator('[data-tab="arenaClient"]').click()
                expect(page.locator('#arenaClientRows .arena-row')).to_have_count(2)
                assert not errors,errors
                browser.close()
                print('Scan browser passed: progress, cancel, task lock, response count, credentials, sudo override, exact discovered-IP import, encrypted persistence. No real LAN scan or install.')
        finally:hold.set();server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
