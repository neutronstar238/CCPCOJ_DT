"""Run as root in an isolated mount namespace. Real bind/rsync; fixture DB only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import database_storage as db
import remote_ops

class FixtureOps:
    def __init__(self,root):self.root=root;self.commands=[];self.running=False;self.fail_copy=False
    def names(self,n):return ['ccpcdb']
    def owned(self,*args):pass
    def inspect_containers(self,n):return [dict(name='ccpcdb',running=self.running,mounts=[dict(Type='bind',Source=str(db.dbpath(self.root)),Destination='/var/lib/mysql')])]
    def write_json(self,p,data):
        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
    def run(self,args,**kwargs):
        self.commands.append(args)
        if args[:2]==['docker','inspect']:return json.dumps([dict(Id='fixture-db',State=dict(ExitCode=0))])
        if args[0]=='systemctl':return ''
        if self.fail_copy and args[0]=='rsync':raise RuntimeError('copy failed')
        return subprocess.check_output(args,text=True).strip()

class DatabaseLinux(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='csg-db-');self.base=Path(self.temp.name)
        self.root=self.base/'project';self.root.mkdir();self.src=db.dbpath(self.root);self.src.mkdir(parents=True)
        (self.src/'auto.cnf').write_text('[auto]\nserver-uuid=fixture\n')
        (self.src/'ibdata1').write_bytes(b'fixture original database\x00'*400)
        os.chmod(self.src/'ibdata1',0o640);os.chown(self.src/'ibdata1',999,999)
        self.dest=self.base/'disk/mysql';self.ops=FixtureOps(self.root)
        self.n=dict(kind='web',site='ccpc')
        self.fstab=self.base/'fstab';self.fstab.write_text('# fixture only\n')
        self.patches=[patch.object(db,'FSTAB',self.fstab),patch.object(db,'DROPINS',self.base/'dropins')]
        for p in self.patches:p.start()
    def tearDown(self):
        if db.is_mount(self.src):subprocess.run(['umount',str(self.src)],check=True)
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def migrate(self):
        p=db.plan(self.ops,self.n,self.root,str(self.dest))
        db.begin(self.ops,self.n,self.root,p)
        return p
    def test_real_copy_bind_rollback_and_original_unchanged(self):
        original=(self.src/'ibdata1').read_bytes();self.migrate()
        self.assertTrue(os.path.samefile(self.src,self.dest))
        self.assertEqual((self.dest/'ibdata1').stat().st_uid,999)
        self.assertEqual((self.dest/'ibdata1').stat().st_mode & 0o777,0o640)
        (self.src/'ibdata1').write_bytes(b'new database startup changes')
        db.finish(self.ops,self.n,self.root,True)
        self.assertFalse(db.is_mount(self.src));self.assertEqual((self.src/'ibdata1').read_bytes(),original)
        self.assertEqual((self.dest/'ibdata1').read_bytes(),b'new database startup changes')
        self.assertEqual(self.fstab.read_text(),'# fixture only\n')
    def test_commit_and_missing_ssd_guard(self):
        self.migrate();db.finish(self.ops,self.n,self.root);db.assert_ready(self.root)
        self.assertIn('RequiresMountsFor=',next((self.base/'dropins').iterdir()).read_text())
        subprocess.run(['umount',str(self.src)],check=True)
        with self.assertRaisesRegex(RuntimeError,'未正确挂载'):db.assert_ready(self.root)
    def test_full_backup_and_update_rollback_keep_external_mount(self):
        self.migrate();db.finish(self.ops,self.n,self.root)
        original=(self.src/'ibdata1').read_bytes()
        archive=self.root/'private/update-backup.tar.gz'
        subprocess.run(['tar','-czpf',str(archive),'-C',str(self.root),'data'],check=True)
        (self.src/'ibdata1').write_bytes(b'failed update')
        (self.src/'new-table.ibd').write_bytes(b'only failed version')
        self.ops.write_json(self.root/'private/update-transaction.json',dict(archive=str(archive),sha256=remote_ops.file_hash(archive),apps=[]))
        with patch.object(remote_ops,'owned'),patch.object(remote_ops,'control'),patch.object(remote_ops,'run',side_effect=self.ops.run):
            remote_ops.update_finish(self.n,{},self.root,rollback=True)
        self.assertTrue(os.path.samefile(self.src,self.dest))
        self.assertEqual((self.dest/'ibdata1').read_bytes(),original)
        self.assertFalse((self.dest/'new-table.ibd').exists())
    def test_copy_failure_recovery_does_not_change_mounts(self):
        p=db.plan(self.ops,self.n,self.root,str(self.dest));self.ops.fail_copy=True
        with self.assertRaisesRegex(RuntimeError,'copy failed'):db.begin(self.ops,self.n,self.root,p)
        db.finish(self.ops,self.n,self.root,True)
        self.assertFalse(db.is_mount(self.src));self.assertTrue((self.src/'auto.cnf').exists())
    def test_reject_nonempty_symlink_and_active_database(self):
        self.dest.mkdir(parents=True);(self.dest/'important').write_text('keep')
        with self.assertRaisesRegex(RuntimeError,'非空'):db.plan(self.ops,self.n,self.root,str(self.dest))
        link=self.base/'link';link.symlink_to(self.dest,target_is_directory=True)
        with self.assertRaises(ValueError):db.plan(self.ops,self.n,self.root,str(link))
        self.dest=self.base/'empty';p=db.plan(self.ops,self.n,self.root,str(self.dest));self.ops.running=True
        with self.assertRaisesRegex(RuntimeError,'已停止'):db.begin(self.ops,self.n,self.root,p)
    def test_unmounted_fstab_disk_refused(self):
        self.fstab.write_text('UUID=not-mounted '+str(self.base/'disk')+' ext4 defaults 0 2\n')
        with self.assertRaisesRegex(RuntimeError,'未按 fstab 挂载'):db.plan(self.ops,self.n,self.root,str(self.dest))
    def test_initialization_empty_only_and_repeat_safe(self):
        (self.src/'auto.cnf').unlink();(self.src/'ibdata1').unlink()
        self.ops.inspect_containers=lambda n:[];self.n['databasePath']=str(self.dest)
        db.initialize(self.ops,self.n,self.root)
        (self.src/'created-by-mysql').write_text('new data')
        db.initialize(self.ops,self.n,self.root)
        self.assertEqual((self.dest/'created-by-mysql').read_text(),'new data')

if __name__=='__main__':unittest.main()
