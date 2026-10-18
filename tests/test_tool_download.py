import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import download_tool
from storage import Store
from engine import Engine
from test_console import config

def binary(marker):
    data = bytearray(100); data[:6] = b'\x7fELF\x02\x01'; data[18:20] = b'\x3e\x00'; data[-1] = marker
    return bytes(data)

class ToolDownloadTests(unittest.TestCase):
    def test_local_filename_preserved_and_version_inferred_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            for name, version in [('csgoj-deploy_2.0.271_linux_amd64', '2.0.271'),
                                  ('csgoj-deploy-2.0.287-exp2-linux-amd64', '2.0.287-exp2')]:
                result = store.tool(binary(3), name)
                self.assertEqual(result['name'], name)
                self.assertEqual(result['version'], version)
                self.assertEqual(result['versionSource'], 'filename')
                self.assertEqual(result['source'], 'local')
            result = store.tool(binary(4), 'renamed-tool')
            self.assertEqual(result['name'], 'renamed-tool')
            self.assertFalse(result.get('version'))

    def test_exact_version_url_content_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp); results = []
            for version, marker in [('2.0.270', 1), ('2.0.287-exp2', 2)]:
                data = binary(marker)
                with patch('artifacts.fetch_json', return_value={'assets':[dict(name=f'csgoj-deploy_{version}_linux_amd64', size=100, browser_download_url=f'https://github.com/SZTU-ACM/CCPCOJ-2/releases/download/{version}/csgoj-deploy_{version}_linux_amd64')]}), patch('artifacts.urllib.request.urlopen', return_value=io.BytesIO(data)) as get:
                    result = download_tool(store, version)
                self.assertEqual(get.call_args.args[0].full_url, f'https://github.com/SZTU-ACM/CCPCOJ-2/releases/download/{version}/csgoj-deploy_{version}_linux_amd64')
                self.assertEqual(result['version'], version)
                self.assertIn(version, result['name']); self.assertEqual(Path(result['localPath']).read_bytes(), data)
                results.append(result)
            self.assertNotEqual(results[0]['sha256'], results[1]['sha256'])
    def test_missing_release_does_not_fall_back_or_change_cache(self):
        store = Mock()
        with patch('artifacts.fetch_json', side_effect=HTTPError('url', 404, 'missing', {}, None)) as get:
            with self.assertRaisesRegex(ValueError, '没有改下载其他版本'): download_tool(store, '2.0.999')
        self.assertEqual(get.call_count, 1); store.tool.assert_not_called()
    def test_invalid_tag_not_used_as_tool_release(self):
        with patch('server.urllib.request.urlopen') as get:
            with self.assertRaises(ValueError): download_tool(Mock(), '../bad')
            get.assert_not_called()
    def test_stale_download_blocks_deploy_before_any_job(self):
        c = config(); c['tool'] = dict(version='2.0.270', sha256='a'*64)
        e = Engine(Mock())
        for action in ('deploy','promote'):
            with self.assertRaisesRegex(ValueError, '不一致'): e.start(action, c, ['web01'])
        self.assertFalse(e.running); self.assertIsNone(e.state['job'])

if __name__ == '__main__': unittest.main()
