import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import threading
import unittest
from unittest.mock import Mock, patch

source=(Path(__file__).resolve().parents[1]/'remote_ops.py').read_text(encoding='utf-8')
function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='saved_image_config_matches')
namespace=dict(subprocess=subprocess,tarfile=tarfile,threading=threading,json=json,hashlib=hashlib)
exec(compile(ast.Module(body=[function],type_ignores=[]),'remote_ops.py','exec'),namespace)

class IdentityTests(unittest.TestCase):
    def test_index_id_fallback_verifies_exact_config_and_layers(self):
        raw=json.dumps(dict(os='linux',architecture='amd64',rootfs=dict(diff_ids=['sha256:layer']))).encode()
        expected='sha256:'+hashlib.sha256(raw).hexdigest()
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as tar:
            for name,data in [('config.json',raw),('manifest.json',json.dumps([dict(Config='config.json',RepoTags=['mysql:8.0.32'])]).encode())]:
                m=tarfile.TarInfo(name);m.size=len(data);tar.addfile(m,io.BytesIO(data))
        def process(*a,**k):return Mock(stdout=io.BytesIO(output.getvalue()),wait=Mock(return_value=0),poll=Mock(return_value=0))
        with patch.object(subprocess,'Popen',side_effect=process):
            verify=namespace['saved_image_config_matches']
            self.assertTrue(verify('mysql:8.0.32',expected,dict(RootFS=dict(Layers=['sha256:layer']))))
            self.assertFalse(verify('mysql:8.0.32','sha256:wrong',dict(RootFS=dict(Layers=['sha256:layer']))))
            self.assertFalse(verify('mysql:8.0.32',expected,dict(RootFS=dict(Layers=['sha256:wrong']))))
            self.assertFalse(verify('nginx:other',expected,dict(RootFS=dict(Layers=['sha256:layer']))))
