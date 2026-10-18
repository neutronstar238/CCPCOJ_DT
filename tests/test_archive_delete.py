import copy,json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from test_artifacts import archive
from artifacts import ImageCache
from storage import Store
from server import make_server

class DeleteArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=ImageCache(Mock(root=self.root))
        self.source=self.root/'original.tar';archive(self.source)
        self.item=self.cache.import_file(self.source)
    def test_requires_explicit_boolean_confirmation(self):
        for value in (False,None,'true',1):
            with self.assertRaises(ValueError):self.cache.delete(self.item['id'],value)
        self.assertTrue(Path(self.item['path']).exists())
    def test_delete_clears_selection_keeps_other_archive_and_original(self):
        archive(self.source,marker=2);other=self.cache.import_file(self.source)
        self.cache.select(self.item['id']);result=self.cache.delete(self.item['id'],True)
        self.assertTrue(result['fileRemoved']);self.assertTrue(result['cleared'])
        self.assertFalse(Path(self.item['path']).exists());self.assertTrue(self.source.exists())
        self.assertTrue(Path(other['path']).exists())
        self.assertEqual(self.cache.catalog()['selected'],{})
        self.assertEqual([a['id'] for a in self.cache.catalog()['archives']],[other['id']])
    def test_delete_unselected_keeps_current_selection(self):
        archive(self.source,marker=2);other=self.cache.import_file(self.source)
        self.assertEqual(self.cache.delete(self.item['id'],True)['cleared'],[])
        self.assertEqual(set(self.cache.catalog()['selected'].values()),{other['id']})
    def test_missing_file_cleans_stale_record(self):
        Path(self.item['path']).unlink()
        self.assertFalse(self.cache.delete(self.item['id'],True)['fileRemoved'])
        self.assertEqual(self.cache.catalog()['archives'],[])
    def test_catalog_failure_restores_file_and_selection(self):
        before=self.cache.catalog()
        with patch.object(self.cache,'write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.cache.delete(self.item['id'],True)
        self.assertEqual(self.cache.catalog(),before);self.assertTrue(Path(self.item['path']).exists())
    def test_file_delete_failure_restores_file_and_catalog(self):
        before=self.cache.catalog();original=Path.unlink
        def fail(path,*args,**kwargs):
            if path.suffix=='.delete-pending':raise PermissionError('locked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail):
            with self.assertRaises(PermissionError):self.cache.delete(self.item['id'],True)
        self.assertEqual(self.cache.catalog(),before);self.assertTrue(Path(self.item['path']).exists())
    def test_invalid_id_and_catalog_path_cannot_delete_source(self):
        for invalid in ('../original','C:/Windows/file','a'*63):
            with self.assertRaises(ValueError):self.cache.delete(invalid,True)
        c=self.cache.catalog();c['archives'][0]['path']=str(self.source);self.cache.write(c)
        self.cache.delete(self.item['id'],True);self.assertTrue(self.source.exists())
    def test_api_rejects_missing_confirmation_and_busy_jobs(self):
        server=make_server(Store(self.root/'api'))
        item=server.artifacts.cache.import_file(self.source)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(body):
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/artifacts/delete',data=json.dumps(body).encode(),headers={'X-CSG-Token':server.token,'Content-Type':'application/json'})
            return urllib.request.urlopen(req)
        try:
            with self.assertRaises(urllib.error.HTTPError):post(dict(id=item['id']))
            for worker in (server.engine,server.ssh_manager,server.artifacts):
                worker.running=True
                with self.assertRaises(urllib.error.HTTPError):post(dict(id=item['id'],confirmed=True))
                worker.running=False
            self.assertTrue(Path(item['path']).exists())
            with post(dict(id=item['id'],confirmed=True)) as response:self.assertEqual(response.status,200)
            self.assertFalse(Path(item['path']).exists())
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
