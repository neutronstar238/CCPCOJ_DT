"""Real browser/UI/API check. Uses documentation IPs; never connects to SSH hosts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from xlsx_fixture import fixture

def main():
    root=Path(__file__).resolve().parents[1]
    (root/'verification').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        server=make_server(Store(temp));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('CSGOJ_BROWSER',str(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe')),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                expect(page.locator('#toolCurrent')).to_contain_text('尚未选择部署程序')
                # Template download + preview + explicit confirm must not dispatch deployment.
                with page.expect_download() as template:page.locator('#template').click()
                assert Path(template.value.path()).read_bytes()[:2]==b'PK'
                imported=Path(temp)/'nodes.xlsx';imported.write_bytes(fixture())
                page.locator('#xlsxFile').set_input_files(str(imported))
                expect(page.locator('#importDialog')).to_be_visible()
                assert page.locator('#importRows tr').count()==4
                assert server.store.load() is None
                page.screenshot(path=str(root/'verification/04-import-preview.png'),full_page=False)
                page.locator('#confirmImport').click();expect(page.locator('.noderow')).to_have_count(4)
                assert server.engine.status()['job'] is None
                assert page.locator('#neverSleep').is_checked()
                # Independent node editing still works after bulk import.
                page.locator('#addJudge').click();page.locator('#editFields [data-key="host"]').fill('192.0.2.22')
                page.locator('#nodeForm button[type="submit"]').click();expect(page.locator('.noderow')).to_have_count(5)
                page.locator('.noderow').nth(4).locator('.remove').click()
                page.locator('#toolFile').set_input_files(os.environ.get('CSGOJ_DEPLOYER_TEST',str(Path.home()/'Downloads/csgoj-deploy_2.0.271_linux_amd64')))
                expect(page.locator('#toolInfo')).to_contain_text('SHA256')
                expect(page.locator('#toolCurrent')).to_contain_text('版本未知')
                # User edits Release without saving: the button must use that exact value.
                downloads=[]
                def fake_download(route):
                    version=route.request.post_data_json['version'];downloads.append(version)
                    metadata=dict(version=version,name=f'csgoj-deploy_{version}_linux_amd64',sha256='a'*64,size=100,localPath='C:\\test\\tools\\cached.bin',source=f'https://github.com/SZTU-ACM/CCPCOJ-2/releases/download/{version}/csgoj-deploy_{version}_linux_amd64')
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(metadata))
                page.route('**/api/tool-download',fake_download)
                page.locator('#toolVersion').fill('2.0.270');page.locator('#downloadTool').click()
                expect(page.locator('#toolInfo')).to_contain_text('部署程序版本：2.0.270')
                expect(page.locator('#toolCurrent')).to_have_text('当前部署将使用：2.0.270')
                expect(page.locator('#toolInfo')).to_contain_text('C:\\test\\tools\\cached.bin')
                assert downloads==['2.0.270']
                page.locator('#toolVersion').fill('2.0.271')
                expect(page.locator('#toolInfo')).to_contain_text('版本不一致')
                expect(page.locator('#toolCurrent')).to_contain_text('当前已选：2.0.270')
                expect(page.locator('#toolCurrent')).to_contain_text('暂不能部署')
                page.locator('#downloadTool').click()
                expect(page.locator('#toolInfo')).to_contain_text('部署程序版本：2.0.271')
                expect(page.locator('#toolCurrent')).to_have_text('当前部署将使用：2.0.271')
                assert downloads==['2.0.270','2.0.271']
                page.locator('#review').click();page.locator('.planrow').first.wait_for()
                assert page.locator('.planrow').count()==4
                saved=server.store.load();assert saved['nodes'][2]['judgeUser']=='j1';assert saved['nodes'][3]['judgeUser']=='j2'
                assert saved['nodes'][3]['role']=='standby'
                page.screenshot(path=str(root/'verification/02-plan.png'),full_page=True)
                page.locator('[data-tab="configure"]').click();page.screenshot(path=str(root/'verification/01-console.png'),full_page=False)
                # Refresh survives fragment removal by keeping only the per-launch token in sessionStorage.
                page.reload();page.locator('.noderow').first.wait_for();assert page.locator('.noderow').count()==4
                local=page.evaluate('Object.keys(localStorage)');assert not local
                page.locator('[data-tab="recovery"]').click()
                with page.expect_download() as d:page.locator('#exportCredentials').click()
                artifact=d.value.path();export=json.loads(Path(artifact).read_text(encoding='utf-8'))
                assert all('password' not in n and 'elevatePassword' not in n for n in export['nodes'])
                assert export['nodes'][0]['secrets']['sqlRoot']
                # Intercept only mutation dispatch: no real deployment, including documentation addresses.
                requested=[]
                def capture(route):
                    requested.append(route.request.post_data_json)
                    route.fulfill(status=200,content_type='application/json',body='{"job":"ui-test-only"}')
                page.route('**/api/run',capture)
                page.locator('[data-tab="monitor"]').click()
                # A host can be reachable while its business service is stopped.
                server.engine.state['nodes']['web01'] = dict(serverStatus='在线（SSH 已连接）', serviceStatus='未运行', canStart=True, status='未运行', checkedAt='2026-10-17T12:00:00')
                server.engine.state['nodes']['web02'] = dict(serverStatus='在线（SSH 已连接）', serviceStatus='未启动（备用）', canStart=False, status='备用缓存就绪')
                expect(page.locator('.startservice')).to_be_visible()
                expect(page.locator('.statuscard').first).to_contain_text('服务器：在线（SSH 已连接）')
                expect(page.locator('.statuscard').first).to_contain_text('Web 服务：未运行')
                page.screenshot(path=str(root/'verification/05-status-switch.png'),full_page=True)
                page.locator('.startservice').click();page.wait_for_timeout(300)
                assert requested[-1]['action']=='startService' and requested[-1]['selected']==['web01']
                page.locator('#targetNode').select_option('web02');page.locator('#sourceWeb').select_option('web01')
                page.locator('#confirmSwitch').check();page.locator('#promote').click()
                page.wait_for_timeout(300);assert requested[-1]['action']=='promote';assert requested[-1]['source']=='web01';assert requested[-1]['selected']==['web02']
                # Editing a prepared judge changes desired count, not the reported live count.
                stored=server.store.load();stored['nodes'][2].update(prepared=True,deployed=True,preparedVersion=stored['version'],deployedVersion=stored['version']);server.store.save(stored)
                page.reload();page.locator('.noderow').nth(2).locator('.edit').click()
                desired=page.locator('#editFields [data-key="desiredPods"]');expect(desired).to_be_enabled();desired.fill('2')
                page.locator('#nodeForm button[type="submit"]').click();page.locator('#save').click()
                expect(page.locator('#notice')).to_contain_text('已加密保存')
                assert server.store.load()['nodes'][2]['pods']==1 and server.store.load()['nodes'][2]['desiredPods']==2
                page.locator('[data-tab="monitor"]').click();page.locator('#targetNode').select_option('pcj01');page.locator('#confirmSwitch').check();page.locator('#resizeJudge').click()
                page.wait_for_timeout(300);assert requested[-1]['action']=='resizeJudge' and requested[-1]['selected']==['pcj01']
                page.locator('#updateVersion').fill('2.0.281-exp')
                page.locator('#checkUpdate').click();page.wait_for_timeout(300)
                assert requested[-1]['action']=='checkUpdate' and requested[-1]['config']['version']=='2.0.281-exp'
                page.locator('#confirmSwitch').uncheck();count=len(requested)
                page.locator('#upgrade').click();expect(page.locator('#notice')).to_contain_text('维护')
                assert len(requested)==count
                page.locator('#confirmSwitch').check();page.locator('#upgrade').click();page.wait_for_timeout(300)
                assert requested[-1]['action']=='upgrade' and requested[-1]['selected']==['pcj01']
                page.locator('#updateVersion').scroll_into_view_if_needed()
                page.screenshot(path=str(root/'verification/07-image-update.png'),full_page=False)
                page.locator('[data-tab="configure"]').click();page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'mobile horizontal overflow'
                page.screenshot(path=str(root/'verification/03-mobile.png'),full_page=False)
                assert not errors,errors
                browser.close()
        finally:server.shutdown();server.server_close()
    print('PASS: blue/white modular UI, XLSX download/preview/confirm without execution, node editing, ELF upload, saved plan, refresh, credential export, combined status/promotion request, mobile layout; no SSH actions.')

if __name__=='__main__':main()
