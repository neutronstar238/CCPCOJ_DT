import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tarfile
from artifacts import ImageCache, archive_images, references, releases, image_tags, pull_reference, download_tool, pull_with_fallback
from engine import Engine
from test_console import config

def archive(path,tags=('csgrandeur/ccpcoj-judge2:2.0.281-exp',),marker=1,arch='amd64',malicious=False,layer=True):
    with tarfile.open(path,'w:gz' if str(path).endswith('.gz') else 'w') as tar:
        def add(name,data):
            m=tarfile.TarInfo(name);m.size=len(data);tar.addfile(m,io.BytesIO(data))
        add('manifest.json',json.dumps([{'Config':'config.json','Layers':['layer.tar'],'RepoTags':tags}]).encode())
        add('config.json',json.dumps(dict(os='linux',architecture=arch,marker=marker)).encode())
        if layer:add('layer.tar',b'layer')
        if malicious:add('../escape',b'x')

class FallbackTests(unittest.TestCase):
    def test_preferred_success_does_not_try_other_sources(self):
        run = Mock(); ref = 'mysql:8.0.32'
        self.assertEqual(pull_with_fallback(ref, 'hub', '', run, Mock()), ref)
        run.assert_called_once_with(['pull', '--platform', 'linux/amd64', ref])

    def test_tencent_failure_falls_back_to_same_hub_tag(self):
        run = Mock(side_effect=[RuntimeError('timeout'), None]); log = Mock()
        ref = 'csgrandeur/ccpcoj-web2:2.0.287-exp'
        self.assertEqual(pull_with_fallback(ref, 'tencent', '', run, log), ref)
        self.assertEqual([c.args[0][-1] for c in run.call_args_list], [pull_reference(ref, 'tencent'), ref])
        self.assertIn('实际下载源：' + ref, [c.args[0] for c in log.call_args_list])

    def test_custom_then_tencent_then_hub_and_all_failed(self):
        run = Mock(side_effect=TimeoutError('timeout')); ref = 'mysql:8.0.32'
        with self.assertRaisesRegex(RuntimeError, '所有下载源均失败'):
            pull_with_fallback(ref, 'custom', 'https://mirror.example', run, Mock())
        self.assertEqual([c.args[0][-1] for c in run.call_args_list],
                         ['mirror.example/library/mysql:8.0.32', pull_reference(ref, 'tencent'), ref])

    def test_hub_failure_falls_back_to_tencent(self):
        run = Mock(side_effect=[RuntimeError('timeout'), None]); ref = 'nginx:1.29.7-alpine'
        self.assertEqual(pull_with_fallback(ref, 'hub', '', run, Mock()), pull_reference(ref, 'tencent'))

    def test_invalid_custom_source_does_not_start_download(self):
        run = Mock()
        with self.assertRaises(ValueError):
            pull_with_fallback('mysql:8.0.32', 'custom', 'https://bad.example/path', run, Mock())
        run.assert_not_called()

class ArchiveTests(unittest.TestCase):
    def test_inventory_distinguishes_web_judge_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=ImageCache(Mock(root=Path(tmp)));path=Path(tmp)/'web.tar'
            archive(path,tags=('csgrandeur/ccpcoj-web2:2.0.288-exp',))
            item=cache.import_file(path);data=cache.describe()
            self.assertTrue(data['archives'][0]['available'])
            self.assertIn('mysql:8.0.32',data['dependencies'])
            self.assertNotIn('csgrandeur/ccpcoj-judge2:2.0.288-exp',data['selected'])
            Path(item['path']).unlink()
            self.assertFalse(cache.describe()['archives'][0]['available'])
    def test_containerd_index_identity_accepts_only_matching_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'oci.tar';ref='csgrandeur/ccpcoj-judge2:2.0.281-exp'
            conf=json.dumps(dict(os='linux',architecture='amd64')).encode();cid='sha256:'+hashlib.sha256(conf).hexdigest()
            manifest=json.dumps(dict(schemaVersion=2,config=dict(digest=cid))).encode();mid='sha256:'+hashlib.sha256(manifest).hexdigest()
            index=json.dumps(dict(schemaVersion=2,manifests=[dict(digest=mid)])).encode();iid='sha256:'+hashlib.sha256(index).hexdigest()
            with tarfile.open(path,'w') as tar:
                def add(name,raw):
                    m=tarfile.TarInfo(name);m.size=len(raw);tar.addfile(m,io.BytesIO(raw))
                add('manifest.json',json.dumps([dict(Config='config.json',RepoTags=[ref],Layers=[])]).encode())
                add('config.json',conf);add('blobs/sha256/'+mid[7:],manifest);add('blobs/sha256/'+iid[7:],index)
                add('index.json',json.dumps(dict(manifests=[dict(digest=iid,annotations={'io.containerd.image.name':'docker.io/'+ref})])).encode())
            ids={};self.assertEqual(archive_images(path,ids)[ref],cid)
            self.assertEqual(ids[ref],[cid,mid,iid])
    def test_tar_gzip_tags_and_selection_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Mock(root=Path(tmp));a=Path(tmp)/'unrelated-name.tar.gz';archive(a)
            cache=ImageCache(store);old=cache.import_file(a)
            archive(a,marker=2);new=cache.import_file(a)
            refs=references('judge','2.0.281-exp')
            self.assertEqual(cache.resolve(refs)[0]['id'],new['id'])
            cache.select(old['id'])
            self.assertEqual(ImageCache(store).resolve(refs)[0]['id'],old['id'])
            self.assertNotEqual(new['images'],old['images'])
    def test_invalid_arch_layer_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'image.tar'
            for kwargs in [dict(arch='arm64'),dict(malicious=True),dict(layer=False)]:
                archive(a,**kwargs)
                with self.assertRaises(ValueError):archive_images(a)
    def test_missing_cache_prevents_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=Engine(Mock(root=Path(tmp)));e.c=config();e.remote=Mock()
            with self.assertRaisesRegex(ValueError,'本地缺少'):e.distribute_images(e.c['nodes'][1])
            e.remote.assert_not_called()
    def test_tamper_prevents_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Mock(root=Path(tmp));a=Path(tmp)/'image.tar';archive(a);item=ImageCache(store).import_file(a)
            Path(item['path']).write_bytes(b'changed');e=Engine(store);e.c=config();e.remote=Mock()
            with self.assertRaisesRegex(ValueError,'已变化'):e.distribute_images(e.c['nodes'][1])
            e.remote.assert_not_called()
    def test_distribution_load_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Mock(root=Path(tmp));a=Path(tmp)/'image.tar';archive(a);ImageCache(store).import_file(a)
            e=Engine(store);e.c=config();r=Mock();e.remote=Mock(return_value=r)
            before=json.dumps(e.c,sort_keys=True);e.distribute_images(e.c['nodes'][1])
            self.assertEqual([c.args[0] for c in r.op.call_args_list],['imagePrerequisite','loadImage'])
            self.assertEqual(json.dumps(e.c,sort_keys=True),before)
            self.assertTrue(r.op.call_args.kwargs['archive'].endswith('.tar'));r.close.assert_called_once()
    def test_web_dependencies_distinct_from_update(self):
        self.assertEqual(len(references('web','2.0.287-exp',True)),4)
        self.assertEqual(len(references('web','2.0.287-exp')),1)
    def test_releases_pagination(self):
        rows=[dict(tag_name=str(i),assets=[]) for i in range(100)]
        with patch('artifacts.fetch_json',side_effect=[rows,[dict(tag_name='last',assets=[])]]) as get:
            self.assertEqual(len(releases()['releases']),101)
            self.assertIn('page=2',get.call_args.args[0])
    def test_tags_all_pages(self):
        with patch('artifacts.fetch_json',side_effect=[{'results':[{'name':'2.0.1'}],'next':'https://hub.docker.com/next'}, {'results':[{'name':'2.0.2-exp'}],'next':None}]):
            self.assertEqual([r['name'] for r in image_tags('web')['tags']],['2.0.1','2.0.2-exp'])
    def test_tencent_custom_and_canonical_tags(self):
        self.assertEqual(pull_reference('mysql:8.0.32','tencent'),'ccr.ccs.tencentyun.com/csgrandeur/mysql:8.0.32')
        self.assertEqual(pull_reference('mysql:8.0.32','custom','https://mirror.example'),'mirror.example/library/mysql:8.0.32')
        self.assertEqual(pull_reference('csgrandeur/ccpcoj-web2:2.0.287-exp','custom','mirror.example'),'mirror.example/csgrandeur/ccpcoj-web2:2.0.287-exp')
        with self.assertRaises(ValueError):pull_reference('mysql:8','custom','https://user:password@mirror.example')
    def test_ambiguous_asset_requires_selection(self):
        assets=[dict(name=n,size=100) for n in ['csgoj-deploy-linux-amd64','csgoj-deploy-2.0.287-exp2-linux-amd64']]
        with patch('artifacts.fetch_json',return_value={'assets':assets}):
            with self.assertRaisesRegex(ValueError,'具体附件'):download_tool(Mock(),'2.0.287-exp2')

if __name__=='__main__':unittest.main()
