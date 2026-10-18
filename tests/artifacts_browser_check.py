"""Browser regression for resource jobs, archive selection and default-empty targets."""
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from server import make_server
from storage import Store
from test_console import config
from test_artifacts import archive

def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);store=Store(root/'state');store.save(config());server=make_server(store)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        image=root/'saved-judge.tar.gz';archive(image)
        releases=[dict(tag_name='2.0.287-exp2',prerelease=True,assets=[dict(name='csgoj-deploy-2.0.287-exp2-linux-amd64',size=1024)])]
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
                page=browser.new_page(viewport={'width':1450,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#{server.token}')
                expect(page.locator('#sshModule')).to_be_visible()
                assert page.locator('.nav').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.tab)') == ['sshModule','configure','artifacts','plan','monitor','recovery','guide']
                page.locator('[data-tab="artifacts"]').click()
                expect(page.locator('#artifacts')).to_be_visible()
                expect(page.locator('#releaseFetch')).to_be_enabled(timeout=20000)
                expect(page.locator('#plan')).to_be_hidden()
                page.locator('[data-tab="plan"]').click()
                expect(page.locator('#plan')).to_be_visible()
                expect(page.locator('#imageUpload')).to_be_hidden()
                page.locator('[data-tab="artifacts"]').click()
                expect(page.locator('#imageUpload')).to_be_visible()
                expect(page.locator('#imageSource')).to_have_value('tencent')
                page.locator('#imageSource').select_option('custom');expect(page.locator('#imageMirror')).to_be_visible()
                page.locator('#imageMirror').fill('mirror.example')
                page.locator('#imageSource').select_option('hub');expect(page.locator('#imageMirrorField')).to_be_hidden()
                with patch('artifacts.fetch_json',return_value=releases):
                    page.locator('#releaseFetch').click()
                    expect(page.locator('#artifactMessage')).to_contain_text('共 1 个 Release',timeout=15000)
                page.locator('#releaseVersion').fill('2.0.999-exp');expect(page.locator('#releaseHint')).to_contain_text('仓库未发布')
                expect(page.locator('#releaseAsset')).to_contain_text('仓库未发布')
                page.locator('#releaseVersion').fill('2.0.287-exp2');page.locator('#releaseVersion').press('Tab')
                expect(page.locator('#releaseAsset')).to_have_value('csgoj-deploy-2.0.287-exp2-linux-amd64')
                from test_tool_download import binary
                tool=root/'csgoj-deploy-2.0.287-exp2-linux-amd64';tool.write_bytes(binary(3))
                page.locator('#toolFile').set_input_files(str(tool))
                expect(page.locator('#toolInfo')).to_contain_text(tool.name,timeout=10000)
                expect(page.locator('#toolInfo')).not_to_contain_text('undefined')
                assert store.load()['tool']['version']=='2.0.287-exp2'
                page.locator('[data-tab="configure"]').click()
                expect(page.locator('#disableGui')).to_be_visible()
                expect(page.locator('#toolFile')).to_be_hidden()
                expect(page.locator('#version')).to_be_hidden()
                page.locator('[data-tab="artifacts"]').click()
                page.locator('#imagePath').fill(str(image));page.locator('#imageImport').click()
                expect(page.locator('#archiveRows')).to_contain_text('csgrandeur/ccpcoj-judge2:2.0.281-exp',timeout=15000)
                expect(page.locator('#distributionNodes input:checked')).to_have_count(0)
                page.locator('#cachedVersions').select_option('2.0.281-exp');expect(page.locator('#imageVersion')).to_have_value('2.0.281-exp')
                page.locator('#imageDistribute').click();expect(page.locator('#artifactMessage')).to_contain_text('请选择分发节点')
                assert not server.engine.running
                page.screenshot(path=str(Path(__file__).resolve().parents[1]/'verification/08-local-images.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow'
                assert not errors,errors
                browser.close()
            print('PASS browser: releases, mirror modes, async archive import, cached version selection, no implicit targets, mobile layout')
        finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
