"""Browser defaults, generic placeholders, draft binding and credential persistence."""
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from ssh_auth import save_login_defaults
from arena_deploy import defaults


def main():
    with tempfile.TemporaryDirectory() as temp:
        store=Store(Path(temp)/'state');key,_=store.key('fixture')
        save_login_defaults(store,str(key))
        server=make_server(store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        c=defaults();c['clients']=[dict(id='fixture',name='待绑定机位',os='linux',prefix=[192,0,2],start=20,end=21,user='root',privateKeyPath=str(key))]
        server.arena.save(c)
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
                page=browser.new_page(viewport=dict(width=1520,height=1100));page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                page.locator('#sshAdd').click()
                expect(page.locator('#sshRows [data-ssh=user]')).to_have_value('root')
                expect(page.locator('#sshRows [data-ssh=privateKeyPath]')).to_have_value(str(key))
                for width in (2048, 1440, 1280, 900, 390):
                    page.set_viewport_size(dict(width=width,height=1100))
                    checks=page.locator('#sshRows tr').evaluate('''row=>{
                        const cells=[...row.cells], key=row.querySelector('[data-ssh=privateKeyPath]').getBoundingClientRect(),
                        unlock=row.querySelector('[data-ssh=keyPassphrase]').getBoundingClientRect(),
                        result=row.querySelector('.sshresult').getBoundingClientRect();
                        return {contained:cells.every(cell=>[...cell.querySelectorAll('input')].every(input=>{
                            const a=input.getBoundingClientRect(),b=cell.getBoundingClientRect();
                            return a.left>=b.left&&a.right<=b.right&&a.top>=b.top&&a.bottom<=b.bottom;
                        })),stacked:unlock.top>=key.bottom+7,clear:unlock.right<=result.left};
                    }''')
                    assert all(checks.values()),(width,checks)
                    if width in (1440,900):
                        # Fixture-only screenshot: hide temporary/user paths and the unlock input value.
                        page.screenshot(path=str(Path(__file__).resolve().parents[1]/'verification'/f'ssh-layout-{width}.png'),
                                        mask=[page.locator('[data-ssh=privateKeyPath]'),page.locator('#dataDir'),page.locator('#sshKeyPaths')])
                page.set_viewport_size(dict(width=1520,height=1100))
                page.locator('[data-tab=configure]').click();page.locator('#addWeb').click()
                expect(page.locator('#nodeDialog [data-key=user]')).to_have_value('root')
                expect(page.locator('#nodeDialog [data-key=privateKeyPath]')).to_have_value(str(key))
                page.evaluate("document.querySelector('#nodeDialog').close()")
                page.locator('[data-tab=arenaClient]').click()
                expect(page.locator('#arenaScanKeyPath')).to_have_value(str(key))
                page.locator('#arenaClient [data-action=preview]').click()
                expect(page.locator('#arenaClientPreview')).to_contain_text('共 2 台目标')
                expect(page.locator('#arenaClientPreview')).to_contain_text('待绑定服务端')
                page.locator('[data-tab=arenaServer]').click();page.locator('#arenaServer [data-add]').click()
                expect(page.locator('#arenaServerRows [data-arena=user]')).to_have_value('root')
                expect(page.locator('#arenaServerRows [data-arena=privateKeyPath]')).to_have_value(str(key))
                page.locator('#arenaServerRows [data-arena=host]').fill('192.0.2.10')
                page.locator('#arenaServer [data-action=save]').click()
                expect(page.locator('#arenaServerNotice')).to_contain_text('已加密保存')
                page.locator('[data-tab=arenaClient]').click()
                # Bind dropdown must track a newly edited server without reloading or losing draft fields.
                page.locator('#arenaBulkServer').select_option(index=1)
                page.locator('[data-bind-unassigned]').click()
                page.locator('#arenaClient [data-action=save]').click()
                expect(page.locator('#arenaClientNotice')).to_contain_text('已加密保存')
                saved=server.arena.config();assert saved['clients'][0]['server']==saved['servers'][0]['id']
                assert saved['clients'][0]['privateKeyPath']==str(key)
                for placeholder in page.locator('[placeholder]').evaluate_all('(els)=>els.map(e=>e.placeholder)'):
                    assert str(key) not in placeholder and 'Users\\' not in placeholder
                assert not page.evaluate('Object.keys(localStorage).length')
                assert server.arena.status()['status']=='尚未执行' and not errors,errors
                browser.close();print('Browser: root/default external key across all login editors; generic placeholders; unbound draft -> bulk binding; encrypted save. No deployment.')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
