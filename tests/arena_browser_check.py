"""Exercise actual page integration, with documentation-only IPs and no deployment."""
import os
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store


def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp:
        server=make_server(Store(temp));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('CSGOJ_BROWSER','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                expect(page.locator('.sidebar .nav').first).to_have_attribute('data-tab','localDeploy')
                page.locator('[data-tab="arenaServer"]').click()
                expect(page.locator('#arenaServer [data-arena-release]')).to_have_value('v1.16.2')
                page.locator('#arenaServer [data-add]').click()
                expect(page.locator('[data-artifact="server-linux"][data-field="url"]')).to_have_value('https://github.com/SZTU-ACM/CSGArena/releases/download/v1.16.2/csgarena_server_linux_amd64_v1.16.2')
                row=page.locator('#arenaServerRows .arena-row')
                row.locator('[data-arena="name"]').fill('主控测试')
                row.locator('[data-arena="host"]').fill('192.0.2.10')
                row.locator('[data-arena="user"]').fill('operator')
                page.locator('#arenaServer [data-action="save"]').click()
                expect(page.locator('#arenaServerNotice')).to_contain_text('已加密保存')
                page.locator('[data-tab="arenaClient"]').click()
                expect(page.locator('#arenaClient [data-arena-consoles] a')).to_have_attribute('href','https://192.0.2.10:39721/')
                page.locator('#arenaDirectClient > summary').click()
                page.locator('#arenaClient [data-add]').click()
                row=page.locator('#arenaClientRows .arena-row')
                row.locator('[data-octet="0"]').fill('192');row.locator('[data-octet="1"]').fill('0');row.locator('[data-octet="2"]').fill('2')
                row.locator('[data-arena="start"]').fill('20');row.locator('[data-arena="end"]').fill('23')
                row.locator('[data-arena="user"]').fill('student')
                expect(row.locator('[data-mirror="1"]')).to_have_value('0')
                expect(row.locator('[data-mirror="2"]')).to_have_value('2')
                page.locator('#arenaClient [data-action="preview"]').click()
                expect(page.locator('#arenaClientPreview')).to_contain_text('共 4 台目标')
                expect(page.locator('#arenaClientPreview')).to_contain_text('https://192.0.2.10:39721')
                page.evaluate("document.querySelector('#dataDir').textContent='独立测试资料目录';document.querySelectorAll('.arena-cache-path').forEach(e=>e.textContent='独立测试资料目录 / arena-cache')")
                page.screenshot(path=str(root/'verification/arena-client.png'),full_page=True)
                page.reload();page.locator('[data-tab="arenaClient"]').click()
                expect(page.locator('#arenaClientRows [data-arena="end"]')).to_have_value('23')
                page.evaluate("document.querySelector('#dataDir').textContent='独立测试资料目录';document.querySelectorAll('.arena-cache-path').forEach(e=>e.textContent='独立测试资料目录 / arena-cache')")
                page.set_viewport_size({'width':800,'height':1100})
                page.screenshot(path=str(root/'verification/arena-client-narrow.png'),full_page=True)
                assert not server.arena.running
                assert server.arena.status()['status']=='尚未执行'
                assert not errors,errors
                print('Arena browser integration passed: range mirror, saved binding, preview, reload, no task dispatched.')
                browser.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
