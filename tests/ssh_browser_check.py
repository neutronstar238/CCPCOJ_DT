"""Independent SSH module in a real browser, against disposable localhost SSH."""
import os
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from ssh_manager import SSHManager
from ssh_check import Fixture

def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);fixture=Fixture(tmp/'fixture');server=make_server(Store(tmp/'state'))
        ssh=tmp/'ssh';ssh.mkdir();source=Store(tmp/'source');key,_=source.key('pcj01');original=key.read_bytes()
        (ssh/'config').write_text(f'Host pcj01\n HostName 192.0.2.20\n User ccpc00\n IdentityFile {key.as_posix()}\n',encoding='utf-8')
        server.ssh_manager=SSHManager(server.store,ssh_dir=ssh)
        threading.Thread(target=server.serve_forever,daemon=True).start();errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('CSGOJ_BROWSER','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                page.locator('[data-tab="sshModule"]').click();page.locator('#sshScan').click()
                expect(page.locator('#sshImportRows')).to_contain_text('pcj01')
                page.locator('#sshConfirmImport').click();expect(page.locator('#sshRows tr')).to_have_count(1)
                row=page.locator('#sshRows tr');row.locator('[data-ssh="host"]').fill('127.0.0.1');row.locator('[data-ssh="user"]').fill('test');row.locator('[data-ssh="sshPort"]').fill(str(fixture.port));row.locator('[data-ssh="password"]').fill('fixture-pass')
                row.locator('.sshselect').check();page.locator('#sshGenerate').click()
                expect(row.locator('.sshresult')).to_contain_text('未连接服务器',timeout=15000)
                page.locator('#sshAuthorize').click()
                expect(row.locator('.sshresult')).to_contain_text('纯密钥登录复验通过',timeout=15000)
                page.locator('#sshTest').click()
                expect(row.locator('.sshresult')).to_have_text('纯密钥登录通过',timeout=15000)
                assert fixture.jobs==[];assert server.store.load() is None
                assert key.read_bytes()==original
                assert (server.store.root/'keys/pcj01').read_bytes()==original
                page.screenshot(path=str(root/'verification/06-ssh-module.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors,errors
                browser.close()
        finally:server.shutdown();server.server_close();fixture.close()
    print('PASS: independent SSH page, scan/import, editable host/user/port, key reuse, real password enrollment, key-only login, no OJ deployment, no source key changes, mobile layout.')

if __name__=='__main__':main()
