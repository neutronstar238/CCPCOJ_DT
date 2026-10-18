import base64
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xlsx_import import preview,sheets
from models import default_config
from xlsx_fixture import fixture
import test_console as fixtures

class ImportTests(unittest.TestCase):
    def test_roles_and_defaults_from_template(self):
        result=preview(fixture(),default_config());c=result['config']
        self.assertEqual(len(c['nodes']),4)
        self.assertEqual([n['role'] for n in c['nodes']],['active','standby','active','standby'])
        self.assertTrue(c['neverSleep']);self.assertEqual(c['nodes'][2]['web'],'web01')
    def test_invalid_backup_code_does_not_become_true(self):
        for value in ('2','yes',''):
            with self.subTest(value=value),self.assertRaises(ValueError):preview(fixture({'D6':value}),default_config())
    def test_duplicate_node_names_rejected(self):
        with self.assertRaisesRegex(ValueError,'重复'):preview(fixture({'B7':'web01'}),default_config())
    def test_merge_preserves_generated_secrets(self):
        first=preview(fixture(),default_config())['config'];second=preview(fixture({'C6':'192.0.2.100'}),first)['config']
        self.assertEqual(first['nodes'][0]['secrets'],second['nodes'][0]['secrets']);self.assertEqual(len(second['nodes']),4)
        self.assertEqual(second['nodes'][0]['host'],'192.0.2.100')
    def test_bad_file_rejected(self):
        with self.assertRaises(ValueError):preview(b'<html>not xlsx</html>',default_config())

class ImportHttpTests(unittest.TestCase):
    setUp=fixtures.HttpTests.setUp
    tearDown=fixtures.HttpTests.tearDown
    request=fixtures.HttpTests.request
    def test_preview_does_not_save_or_execute(self):
        h={'X-CSG-Token':self.s.token,'Content-Type':'application/json'}
        payload={'file':base64.b64encode(fixture()).decode(),'config':default_config()}
        with self.request('/api/xlsx-preview',json.dumps(payload).encode(),h) as r:result=json.load(r)
        self.assertEqual(len(result['changes']),4)
        self.assertIsNone(self.s.store.load());self.assertFalse(self.s.engine.running)
        with self.request('/api/save',json.dumps({'config':result['config']}).encode(),h):pass
        self.assertEqual(len(self.s.store.load()['nodes']),4);self.assertFalse(self.s.engine.running)

if __name__=='__main__':unittest.main()
