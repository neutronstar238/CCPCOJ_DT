import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_artifacts import archive
from artifacts import ImageCache
from image_discovery import discover, docker_inventory, docker_run, export_docker

class DiscoveryTests(unittest.TestCase):
    def test_reads_manifest_not_filename_and_includes_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive(root/'fake-2.0.277.tar',tags=('csgrandeur/ccpcoj-web2:2.0.280-exp',))
            archive(root/'arm.tar',arch='arm64')
            archive(root/'dependency.tar',tags=('mysql:8.0.32',))
            (root/'broken.tar').write_bytes(b'not a tar')
            with patch('image_discovery.docker_inventory',return_value=dict(available=False,images=[],message='offline')):
                result=discover(SimpleNamespace(root=root),[root,root])
            self.assertEqual(len(result['archives']),2)
            refs={ref for item in result['archives'] for ref in item['images']}
            self.assertIn('csgrandeur/ccpcoj-web2:2.0.280-exp',refs)
            self.assertIn('mysql:8.0.32',refs)
            self.assertEqual(len(result['rejected']),2)
            self.assertFalse(result['docker']['available'])

    def test_docker_inventory_checks_platform_and_keeps_versions(self):
        rows='\n'.join(json.dumps(dict(Repository='csgrandeur/ccpcoj-web2',Tag=tag)) for tag in ('2.0.277-exp','2.0.280-exp','arm'))
        images=[dict(Os='linux',Architecture=arch,Id='sha256:'+'a'*64,Size=12,RepoTags=['csgrandeur/ccpcoj-web2:'+tag]) for tag,arch in [('2.0.277-exp','amd64'),('2.0.280-exp','amd64'),('arm','arm64')]]
        with patch('image_discovery.docker_run',side_effect=[rows,json.dumps(images)]): result=docker_inventory()
        self.assertTrue(result['available'])
        self.assertEqual([x['ref'] for x in result['images']],['csgrandeur/ccpcoj-web2:2.0.277-exp','csgrandeur/ccpcoj-web2:2.0.280-exp'])

    def test_docker_unavailable_is_not_reported_as_no_images(self):
        with patch('image_discovery.docker_run',side_effect=ValueError('engine unavailable')):
            result=docker_inventory()
        self.assertFalse(result['available']);self.assertIn('unavailable',result['message'])

    def test_one_unavailable_platform_does_not_hide_other_local_images(self):
        rows='\n'.join(json.dumps(dict(Repository='mysql',Tag=tag)) for tag in ('good','arm'))
        good=dict(Os='linux',Architecture='amd64',Id='sha256:'+'a'*64,RepoTags=['mysql:good'])
        with patch('image_discovery.docker_run',side_effect=[rows,ValueError('platform missing'),ValueError('arm only'),json.dumps([good])]):
            result=docker_inventory()
        self.assertTrue(result['available']);self.assertEqual(result['images'][0]['ref'],'mysql:good')
        self.assertIn('mysql:arm',result['message'])

    def test_dependency_inventory_uses_independent_versions_and_filters_unrelated_images(self):
        refs=['mysql:8.0.32','nginx:1.29.7-alpine','phpmyadmin:5.2.3']
        rows='\n'.join(json.dumps(dict(Repository=ref.split(':')[0],Tag=ref.split(':')[1])) for ref in refs+['unrelated:latest'])
        images=[dict(Os='linux',Architecture='amd64',Id='sha256:'+'a'*64,Size=12,RepoTags=[ref]) for ref in refs]
        with patch('image_discovery.docker_run',side_effect=[rows,json.dumps(images)]) as run:
            result=docker_inventory()
        self.assertEqual({image['ref'] for image in result['images']},set(refs))
        self.assertNotIn('unrelated:latest',run.call_args.args[0])

    def test_command_pins_local_endpoint_ignores_remote_context(self):
        with patch('image_discovery.shutil.which',return_value='docker'),patch.dict('os.environ',{'DOCKER_HOST':'tcp://remote','DOCKER_CONTEXT':'remote'}),patch('image_discovery.subprocess.run') as run:
            run.return_value=SimpleNamespace(returncode=0,stdout='[]')
            docker_run(['image','ls'])
        self.assertEqual(run.call_args.args[0][1],'--host')
        self.assertNotIn('DOCKER_HOST',run.call_args.kwargs['env'])
        self.assertNotIn('DOCKER_CONTEXT',run.call_args.kwargs['env'])

    def test_export_rejects_changed_tag_before_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=ImageCache(SimpleNamespace(root=Path(tmp)))
            with patch('image_discovery.docker_run',return_value=json.dumps([dict(Id='sha256:'+'b'*64)])) as run:
                with self.assertRaisesRegex(ValueError,'变化'): export_docker(cache,'csgrandeur/ccpcoj-web2:2.0.280-exp','sha256:'+'a'*64,lambda _:None)
                self.assertEqual(run.call_count,1)

    def test_export_imports_verified_archive_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=ImageCache(SimpleNamespace(root=Path(tmp)))
            from artifacts import archive_images
            fixture=Path(tmp)/'fixture.tar';ref='mysql:8.0.32'
            archive(fixture,tags=(ref,));iid=archive_images(fixture)[ref]
            def run(args,**kwargs):
                if args[:2]==['image','inspect']:return json.dumps([dict(Id=iid,Os='linux',Architecture='amd64',Size=10)])
                self.assertEqual(args[:2],['image','save'])
                Path(args[args.index('--output')+1]).write_bytes(fixture.read_bytes());return ''
            with patch('image_discovery.docker_run',side_effect=run): result=export_docker(cache,ref,iid,lambda _:None)
            self.assertTrue(Path(result['path']).is_file());self.assertIn(ref,result['images'])
            self.assertFalse(list(cache.root.glob('docker-export-*')))

    def test_export_reuses_verified_selected_archive_without_docker_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=ImageCache(SimpleNamespace(root=Path(tmp)))
            fixture=Path(tmp)/'fixture.tar';ref='mysql:8.0.32'
            archive(fixture,tags=(ref,));item=cache.import_file(fixture)
            iid=item['images'][ref]
            with patch('image_discovery.docker_run',return_value=json.dumps([dict(Id=iid,Os='linux',Architecture='amd64',Size=10)])) as run:
                result=export_docker(cache,ref,iid,lambda _:None)
            self.assertEqual(result['id'],item['id']);self.assertEqual(run.call_count,1)

if __name__=='__main__':unittest.main()
