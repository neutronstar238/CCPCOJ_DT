import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
from arena_release import DEFAULT_TAG, DEFAULT_SHA256, KINDS, asset_name, asset_url, default_artifacts, fetch_release, release_tag, authenticated_download, list_releases, release_summary
from arena_deploy import ArenaDeployment, defaults, normalize, cache_artifact


def metadata():
    return dict(tag_name=DEFAULT_TAG,draft=False,assets=[dict(name=asset_name(k,DEFAULT_TAG),browser_download_url=asset_url(k,DEFAULT_TAG),digest='sha256:'+DEFAULT_SHA256[k]) for k in KINDS])


class ReleaseTests(unittest.TestCase):
    def test_all_pages_are_read_including_prereleases_and_drafts(self):
        first=[dict(id=i+1,tag_name=f'v1.0.{i}',assets=[]) for i in range(100)]
        last=metadata();last.update(id=101,prerelease=True)
        draft=metadata();draft.update(id=102,tag_name='v2.0.0',draft=True)
        with patch('arena_release.urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(first).encode()),io.BytesIO(json.dumps([last,draft]).encode())]) as fetch:
            r=list_releases()
        self.assertEqual(r['total'],102);self.assertEqual(fetch.call_count,2)
        self.assertIn('page=2',fetch.call_args_list[1].args[0].full_url)
        self.assertTrue(r['releases'][100]['available']);self.assertTrue(r['releases'][100]['prerelease'])
        self.assertFalse(r['releases'][101]['available']);self.assertTrue(r['releases'][101]['draft'])

    def test_private_pagination_reuses_auth_and_page_failure_is_not_silently_partial(self):
        first=[dict(id=i+1,tag_name=f'v1.0.{i}',assets=[]) for i in range(100)]
        with patch('arena_release.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',404,'private',{},None)) as anonymous,patch('arena_release.gh_command',side_effect=[json.dumps(first),'[]']) as gh:
            self.assertEqual(list_releases()['total'],100)
            self.assertEqual(anonymous.call_count,1);self.assertIn('page=2',gh.call_args_list[1].args[0][1])
        with patch('arena_release.urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(first).encode()),urllib.error.HTTPError('url',503,'unavailable',{},None)]):
            with self.assertRaisesRegex(ValueError,'不完整'):list_releases()

    def test_list_marks_missing_checksums_and_unsupported_tags(self):
        data=metadata();data['assets'][0]['digest']=None
        summary=release_summary(data)
        self.assertFalse(summary['available']);self.assertEqual(sum(a['available'] for a in summary['assets']),3)
        self.assertIn('SHA256',summary['reason'])
        data['tag_name']='legacy-build'
        self.assertFalse(release_summary(data)['available'])

    def test_exact_versioned_names_and_preconfigured_checksums(self):
        self.assertEqual(asset_name('server-linux','1.11.0'),'csgarena_server_linux_amd64_v1.11.0')
        self.assertEqual(asset_name('client-windows','v1.11.0'),'csgarena_client_windows_amd64_v1.11.0.exe')
        self.assertEqual(release_tag('https://github.com/SZTU-ACM/CSGArena/releases/tag/v1.11.0'),'v1.11.0')
        for k in KINDS:self.assertEqual(defaults()['artifacts'][k]['sha256'],DEFAULT_SHA256[k])
        for v in ('https://github.com/other/repo/releases/tag/v1.11.0','../x','v1.11.0;foo'):
            with self.assertRaises(ValueError):release_tag(v)
        c=normalize(dict(releaseTag='v2.0.0'))
        self.assertTrue(all(not a['url'] for a in c['artifacts'].values()))
        with self.assertRaisesRegex(ValueError,'不一致'):normalize(dict(releaseTag='v2.0.0',artifacts=default_artifacts()))

    def test_release_requires_all_assets_and_github_digests(self):
        data=metadata()
        with patch('arena_release.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
            self.assertEqual(fetch_release(DEFAULT_TAG)['artifacts'],default_artifacts())
        for edit in ('missing','checksum','wrongurl'):
            bad=copy.deepcopy(data)
            if edit=='missing':bad['assets'].pop()
            elif edit=='checksum':bad['assets'][0]['digest']=None
            else:bad['assets'][0]['browser_download_url']='https://example.org/x'
            with patch('arena_release.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(bad).encode())),self.assertRaises(ValueError):fetch_release(DEFAULT_TAG)

    def test_private_metadata_uses_gh_without_extracting_token(self):
        error=urllib.error.HTTPError('https://api.github.com',404,'Not Found',{},None)
        with patch('arena_release.urllib.request.urlopen',side_effect=error),patch('arena_release.gh_command',return_value=json.dumps(metadata())) as gh:
            fetch_release(DEFAULT_TAG)
            gh.assert_called_once_with(['api','repos/SZTU-ACM/CSGArena/releases/tags/'+DEFAULT_TAG])

    def test_only_exact_repository_assets_can_use_authenticated_download(self):
        with tempfile.TemporaryDirectory() as tmp,patch('arena_release.gh_command') as gh:
            dest=Path(tmp)/'download'
            for url in ('https://evil.invalid/x','https://github.com/other/repo/releases/download/v1.11.0/x','https://github.com/SZTU-ACM/CSGArena/releases/download/v1.11.0/*'):
                with self.assertRaises(ValueError):authenticated_download(url,dest)
            gh.assert_not_called()
            def download(args):
                (Path(args[-1])/asset_name('server-linux',DEFAULT_TAG)).write_bytes(b'\x7fELFfixture')
            gh.side_effect=download
            authenticated_download(asset_url('server-linux',DEFAULT_TAG),dest)
            self.assertEqual(dest.read_bytes(),b'\x7fELFfixture')
            self.assertFalse(list(Path(tmp).glob('arena-gh-*')))

    def test_authenticated_download_still_checks_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            def download(url,dest):dest.write_bytes(b'\x7fELFwrong-release')
            with patch('arena_deploy.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',404,'Not Found',{},None)),patch('arena_deploy.authenticated_download',side_effect=download):
                with self.assertRaisesRegex(ValueError,'SHA256'):cache_artifact(Path(tmp),'server-linux',default_artifacts()['server-linux'])
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_legacy_blank_config_migrates_but_custom_source_survives(self):
        with tempfile.TemporaryDirectory() as tmp,patch('arena_deploy.dpapi',side_effect=lambda b,*a:b):
            manager=ArenaDeployment(Mock(root=Path(tmp)))
            c=dict(distro='Ubuntu',servers=[],clients=[],artifacts={'server-linux':dict(path='D:/custom',url='',sha256='')})
            manager.path.write_text(json.dumps(c))
            loaded=manager.config()
            self.assertEqual(loaded['artifacts']['server-linux']['path'],'D:/custom')
            self.assertEqual(loaded['artifacts']['client-windows'],default_artifacts()['client-windows'])
            c['artifacts']['server-windows'] = {'url':asset_url('server-windows','v1.11.0')}
            manager.path.write_text(json.dumps(c))
            self.assertEqual(manager.config()['releaseTag'],'v1.11.0')


if __name__=='__main__':unittest.main()
