"""Exercise version listing and explicit selection with deterministic release metadata."""
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from arena_release import KINDS, asset_name, asset_url, release_summary, release_asset


def main():
    releases=[]
    for tag in ('v1.11.0','v1.10.0'):
        releases.append(dict(tag_name=tag,draft=False,prerelease=False,published_at='2026-11-06T00:00:00Z',assets=[dict(name=asset_name(k,tag),browser_download_url=asset_url(k,tag),digest='sha256:'+'b'*64) for k in KINDS]))
    releases.append(dict(tag_name='v0.1.0',draft=False,assets=[]))
    def selected(tag):
        data=next(r for r in releases if r['tag_name']==tag)
        return dict(tag=tag,artifacts={k:release_asset(data,k,tag) for k in KINDS})
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp,patch('server.arena_releases',return_value=dict(total=3,releases=[release_summary(r) for r in releases])),patch('server.arena_release',side_effect=selected):
        srv=make_server(Store(tmp));thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start();errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{srv.server_port}/#{srv.token}')
                page.locator('[data-tab="arenaServer"]').click()
                page.locator('#arenaServer [data-action="releases"]').click()
                expect(page.locator('#arenaServer [data-release-list-status]')).to_contain_text('共 3 个 Release')
                choice=page.locator('#arenaServer [data-arena-release-choice]')
                expect(choice).to_have_value('')
                expect(choice.locator('[value="v0.1.0"]')).to_have_attribute('disabled','')
                choice.select_option('v1.10.0')
                expect(page.locator('#arenaClient [data-arena-release-choice]')).to_have_value('v1.10.0')
                page.locator('#arenaServer [data-action="selectedRelease"]').click()
                expect(page.locator('#arenaServer [data-arena-release]')).to_have_value('v1.10.0')
                expect(page.locator('[data-artifact="server-linux"][data-field="url"]')).to_have_value(asset_url('server-linux','v1.10.0'))
                page.locator('#arenaServer [data-action="save"]').click()
                expect(page.locator('#arenaServerNotice')).to_contain_text('已加密保存')
                assert srv.arena.config()['releaseTag']=='v1.10.0'
                choice.select_option('v1.11.0')
                page.locator('#arenaServer [data-action="distribute"]').click()
                expect(page.locator('#notice')).to_contain_text('应用所选版本')
                assert srv.arena.status()['status']=='尚未执行'
                choice.select_option('v1.10.0')
                page.evaluate("document.querySelector('#dataDir').textContent='独立测试资料目录';document.querySelectorAll('.arena-cache-path').forEach(e=>e.textContent='独立测试资料目录 / arena-cache')")
                panel=page.locator('#arenaServer > .panel').nth(1)
                panel.screenshot(path=str(root/'verification/arena-releases.png'))
                assert not errors,errors
                browser.close()
                print('Release browser passed: all-version list, unavailable versions disabled, explicit older-version selection, shared choice, saved exact URLs, unapplied choice blocks push. No download or deployment.')
        finally:srv.shutdown();srv.server_close();thread.join()


if __name__=='__main__':main()
