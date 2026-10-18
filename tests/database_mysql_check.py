"""Opt-in integration: temporary MySQL container, no published ports, QA WSL only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import database_storage as db
import remote_ops as ops

def main():
    with tempfile.TemporaryDirectory(prefix='csg-mysql-qa-') as temp:
        base=Path(temp);root=base/'project';src=db.dbpath(root);src.mkdir(parents=True)
        site='csgqa'+uuid.uuid4().hex[:8];name=site+'db';password=uuid.uuid4().hex
        n=dict(kind='web',site=site);dest=base/'ssd/mysql'
        fstab=base/'fstab';fstab.write_text('# QA fixture\n')
        class Adapter:
            def run(self,args,**kwargs):
                if args[0]=='systemctl':return ''
                return ops.run(args,**kwargs)
            def owned(self,*args):pass
            def names(self,n):return [name]
            def inspect_containers(self,n):
                x=json.loads(ops.run(['docker','inspect',name]))[0]
                return [dict(name=name,running=x['State']['Running'],mounts=x['Mounts'])]
            write_json=staticmethod(ops.write_json)
        adapter=Adapter()
        sql=lambda q:ops.run(['docker','exec',name,'sh','-c','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N -e "$1"','sh',q],timeout=15)
        try:
            ops.run(['docker','run','-d','--name',name,'--network','none','-e','MYSQL_ROOT_PASSWORD='+password,
                     '--mount','type=bind,src='+str(src)+',dst=/var/lib/mysql','mysql:8.0.32'])
            for i in range(90):
                try:
                    if sql('SELECT 1')=='1':break
                except RuntimeError:pass
                time.sleep(1)
            else:raise RuntimeError('fixture MySQL not ready')
            sql("CREATE DATABASE fixture; CREATE TABLE fixture.rows_test(id INT PRIMARY KEY, value VARCHAR(30)); INSERT INTO fixture.rows_test VALUES(1,'original'),(2,'preserved')")
            before=sql('SELECT * FROM fixture.rows_test ORDER BY id')
            original=json.loads(ops.run(['docker','inspect',name]))[0]
            with patch.object(db,'FSTAB',fstab),patch.object(db,'DROPINS',base/'dropins'):
                plan=db.plan(adapter,n,root,str(dest))
                ops.run(['docker','stop','-t','120',name])
                db.begin(adapter,n,root,plan)
                db.validate(adapter,n,root)
                assert sql('SELECT * FROM fixture.rows_test ORDER BY id')==before
                db.finish(adapter,n,root)
                db.assert_ready(root)
                now=json.loads(ops.run(['docker','inspect',name]))[0]
                assert original['Id']==now['Id'] and original['Config']['Env']==now['Config']['Env']
                sql("INSERT INTO fixture.rows_test VALUES(3,'new-ssd-write')")
                ops.run(['docker','stop','-t','120',name]);ops.run(['docker','start',name])
                for i in range(60):
                    try:
                        if sql('SELECT COUNT(*) FROM fixture.rows_test')=='3':break
                    except RuntimeError:pass
                    time.sleep(1)
                else:raise RuntimeError('restart lost rows')
            print('PASS: real MySQL migration, rows/password/container preserved, SSD writes survive restart')
        finally:
            ops.run(['docker','rm','-f',name],check=False)
            if db.is_mount(src):ops.run(['umount',str(src)])

if __name__=='__main__':main()
