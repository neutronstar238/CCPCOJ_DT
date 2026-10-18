import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import tarfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registry_download import Registry, SafeRedirect, download_image, network_routes, is_http_proxy
from local_image_cache import LocalLayers
from artifacts import archive_images, ImageCache


def digest(raw): return 'sha256:'+hashlib.sha256(raw).hexdigest()


class DownloadTests(unittest.TestCase):
    def test_four_concurrent_layers_keep_manifest_order_and_reuse(self):
        plain=[('layer'+str(i)).encode() for i in range(6)]
        zipped=[gzip.compress(x) for x in plain]
        conf=json.dumps(dict(os='linux',architecture='amd64',rootfs=dict(diff_ids=[digest(x) for x in plain]))).encode()
        manifest=dict(schemaVersion=2,config=dict(digest=digest(conf),size=len(conf)),layers=[dict(digest=digest(x),size=len(x),mediaType='application/vnd.oci.image.layer.v1.tar+gzip') for x in zipped])
        active=0; peak=0; lock=threading.Lock(); barrier=threading.Barrier(4)
        data={digest(x):x for x in zipped+[conf]}
        def opened(suffix):
            nonlocal active,peak
            d=suffix.removeprefix('/blobs/')
            if d != digest(conf):
                with lock: active+=1;peak=max(peak,active)
                if d in [digest(x) for x in zipped[:4]]: barrier.wait(timeout=5)
                time.sleep(.02)
                with lock: active-=1
            return io.BytesIO(data[d])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=Registry('org/test:v1',{},Mock())
            with patch.object(r,'manifest',side_effect=lambda *a:json.loads(json.dumps(manifest))),patch.object(r,'open',side_effect=opened) as request:
                r.download('org/test:v1',root,root/'out.tar')
                self.assertEqual(peak,4)
                with tarfile.open(root/'out.tar') as t:
                    entries=json.load(t.extractfile('manifest.json'))[0]['Layers']
                    self.assertEqual(entries,['blobs/sha256/'+digest(x)[7:] for x in zipped])
                request.reset_mock();r.download('org/test:v1',root,root/'out.tar');request.assert_not_called()

    def test_control_port_never_used_as_proxy(self):
        with patch('registry_download.socket.create_connection') as connect:
            self.assertFalse(is_http_proxy(53000));connect.assert_not_called()
        with patch('registry_download.urllib.request.getproxies',return_value={'https':'http://127.0.0.1:53000'}),patch('registry_download.subprocess.run',return_value=Mock(stdout=b'[53000,12000,12001]')),patch('registry_download.is_http_proxy',side_effect=lambda p:p==12000) as probe:
            routes=network_routes()
            self.assertFalse(any('53000' in str(x) for x in routes))
            self.assertFalse(any(c.args==(53000,) for c in probe.call_args_list))

    def test_reuse_old_archive_and_reject_corrupt_layer(self):
        plain=b'shared across versions';raw=gzip.compress(plain)
        desc=dict(digest=digest(raw),size=len(raw));diff=digest(plain)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'blobs';cache.mkdir();reuse=LocalLayers(root,Mock())
            def archive(content, classic=False):
                path=root/'old.tar'
                with tarfile.open(path,'w') as t:
                    files={'blobs/sha256/'+digest(raw)[7:]:content}
                    if classic:
                        files={'layer.tar':content,'config.json':json.dumps(dict(os='linux',architecture='amd64',rootfs=dict(diff_ids=[diff]))).encode(),'manifest.json':json.dumps([dict(Config='config.json',Layers=['layer.tar'],RepoTags=['org/test:old'])]).encode()}
                    for name,data in files.items():
                        m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
                return path
            missing={desc['digest']:(desc,diff)}
            reuse.from_archive(archive(b'x'*len(raw)),missing,cache);self.assertTrue(missing)
            reuse.from_archive(archive(raw),missing,cache);self.assertFalse(missing)
            self.assertEqual((cache/digest(raw)[7:]).read_bytes(),raw)
            missing={desc['digest']:(desc,diff)}
            reuse.from_archive(archive(plain,True),missing,cache);self.assertFalse(missing)
            self.assertEqual((cache/(diff[7:]+'.layer')).read_bytes(),plain)

    def test_archive_integrity_and_platform_selection(self):
        layer=b'example layer contents'; compressed=gzip.compress(layer)
        config=json.dumps(dict(os='linux',architecture='amd64',rootfs=dict(diff_ids=[digest(layer)]))).encode()
        manifest=json.dumps(dict(schemaVersion=2,config=dict(digest=digest(config),size=len(config)),
            layers=[dict(digest=digest(compressed),size=len(compressed),mediaType='application/vnd.oci.image.layer.v1.tar+gzip')])).encode()
        index=json.dumps(dict(manifests=[dict(digest=digest(manifest),platform=dict(os='linux',architecture='amd64')),
            dict(digest='sha256:'+'f'*64,platform=dict(os='linux',architecture='arm64'))])).encode()
        contents={'/manifests/v1':index,'/manifests/'+digest(manifest):manifest,
            '/blobs/'+digest(config):config,'/blobs/'+digest(compressed):compressed}
        r=Registry('org/test:v1',{},Mock())
        def opened(suffix):
            f=io.BytesIO(contents[suffix]); f.headers={}; return f
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'open',side_effect=opened) as request:
            root=Path(tmp); output=root/'image.tar';r.download('org/test:v1',root,output)
            self.assertEqual(archive_images(output), {'org/test:v1':digest(config)})
            self.assertFalse(any('referrers' in c.args[0] for c in request.call_args_list))
            # A second run fetches the manifest but reuses checked layers.
            request.reset_mock();r.download('org/test:v1',root,output)
            self.assertEqual(len(request.call_args_list),2)
            cache=ImageCache(Mock(root=root));cache.import_file(output)
            self.assertEqual(len(cache.resolve(['org/test:v1'])),1)

    def test_corrupt_blob_rejected_and_not_published(self):
        r=Registry('org/test:v1',{},Mock()); data=b'expected'
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'open',side_effect=lambda _:io.BytesIO(b'corrupt!')), patch('registry_download.time.sleep'):
            with self.assertRaisesRegex(ValueError,'SHA256'):
                r.blob(dict(digest=digest(data),size=len(data)),Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_wrong_manifest_digest_rejected(self):
        r=Registry('org/test:v1',{},Mock());f=io.BytesIO(b'{}');f.headers={}
        with patch.object(r,'open',return_value=f):
            with self.assertRaisesRegex(ValueError,'清单 SHA256'):
                r.manifest('v1','sha256:'+'0'*64)

    def test_redirect_drops_auth_and_upgrades_https(self):
        req=urllib.request.Request('https://registry.example/v2/x/blobs/a',headers={'Authorization':'Bearer SECRET'})
        redirected=SafeRedirect().redirect_request(req,None,307,'',{},'http://cdn.example/blob')
        self.assertEqual(redirected.full_url,'https://cdn.example/blob')
        self.assertFalse(redirected.has_header('Authorization'))

    def test_network_failure_uses_next_route_then_exact_tag_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch('registry_download.network_routes',return_value=[('proxy',{'https':'http://127.0.0.1:1'}),('direct',{})]), patch.object(Registry,'download',side_effect=[TimeoutError(),TimeoutError(),None]) as download:
            result=download_image('org/test:v1',['mirror.example/org/test:v1','org/test:v1'],Path(tmp),Path(tmp)/'out.tar',Mock())
            self.assertEqual(result,'org/test:v1');self.assertEqual(download.call_count,3)
            self.assertTrue(all(c.args[0]=='org/test:v1' for c in download.call_args_list))

    def test_missing_tag_moves_to_next_source_and_preserves_old_cache(self):
        error=urllib.error.HTTPError('https://example',404,'not found',{},None)
        with tempfile.TemporaryDirectory() as tmp, patch('registry_download.network_routes',return_value=[('proxy',{}),('direct',{})]), patch.object(Registry,'download',side_effect=[error,None]) as download:
            output=Path(tmp)/'out.tar';output.write_bytes(b'partial')
            download_image('org/test:v1',['mirror.example/org/test:v1','org/test:v1'],Path(tmp),output,Mock())
            self.assertEqual(download.call_count,2);self.assertFalse(output.exists())


if __name__=='__main__': unittest.main()
