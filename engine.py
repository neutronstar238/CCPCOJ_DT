import copy
import datetime
import hashlib
import json
import shutil
from pathlib import Path
import threading
import time
import traceback
import uuid
from models import normalize, plan, redact
from transport import Remote
from oj import Client, url
from storage import restrict
from artifacts import ImageCache, references, file_hash

class Engine:
    def __init__(self, store, remote_factory=Remote, client_factory=Client):
        self.store, self.remote_factory, self.client_factory = store, remote_factory, client_factory
        self.lock = threading.Lock(); self.running = False; self.cancel = threading.Event()
        self.state = dict(running=False, status='等待配置', logs=[], nodes={}, job=None)
    def log(self, msg):
        line = redact(str(msg), getattr(self, 'c', {}))
        line = datetime.datetime.now().strftime('%H:%M:%S') + '  ' + line
        with self.lock:
            self.state['logs'].append(line)
            self.state['logs'] = self.state['logs'][-3000:]
        if getattr(self, 'logpath', None):
            with self.logpath.open('a', encoding='utf-8') as f: f.write(line + '\n')
    def status(self):
        with self.lock: return copy.deepcopy(self.state)
    def remote(self, n, bootstrap=False):
        return self.remote_factory(n, self.store, self.log).connect(bootstrap)
    def op(self, n, action, extra=None, tool=None):
        r = self.remote(n)
        try: return r.op(action, self.c, tool=tool, extra=extra)
        finally: r.close()
    def node(self, nid):
        return next(n for n in self.c['nodes'] if n['id'] == nid)
    def save(self): self.store.save(self.c)
    def checkpoint(self):
        if self.cancel.is_set(): raise RuntimeError('已在节点边界停止；已完成的节点保留。迁移关键阶段不接受中断。')
    def start(self, action, config, selected=None, source=None):
        db_actions=('databaseInspect','databaseCheck','databaseMigrate','databaseRecover')
        if action not in db_actions+('syncVersions', 'batchUpdate', 'distribute', 'deploy', 'health', 'startService', 'resizeJudge', 'backup', 'promote', 'restoreHost', 'rebind', 'checkUpdate', 'upgrade', 'recoverUpdate', 'adoptInspect', 'adopt'): raise ValueError('未知操作')
        c = normalize(config)
        if action in ('deploy', 'promote', 'checkUpdate', 'upgrade', 'distribute') and not c.get('version'):
            raise ValueError('请明确选择本次镜像版本；不会默认使用旧版本')
        if selected is None and action in ('health','syncVersions'):
            selected = [n['id'] for n in c['nodes']]
        if not selected: raise ValueError('请选择至少一个节点；不会默认操作全部节点')
        selected = list(dict.fromkeys(selected))
        if not set(selected) <= {n['id'] for n in c['nodes']}: raise ValueError('节点不存在')
        if action in db_actions:
            if len(selected)!=1 or next(n for n in c['nodes'] if n['id']==selected[0])['kind']!='web': raise ValueError('请选择一个 Web 节点')
            if action in ('databaseMigrate','databaseRecover') and (not isinstance(source,dict) or source.get('confirmed') is not True):
                raise ValueError('请二次确认数据库维护操作')
        if action=='batchUpdate':
            for n in c['nodes']:
                if n['id'] in selected and not n.get('targetVersion'): raise ValueError(n['id']+'：请明确选择目标版本')
        if action in ('startService', 'resizeJudge', 'backup', 'promote', 'restoreHost', 'rebind', 'checkUpdate', 'upgrade', 'recoverUpdate', 'adoptInspect', 'adopt') and len(selected) != 1: raise ValueError('本操作请选择一个节点')
        if action in ('deploy', 'promote') and not c.get('tool'): raise ValueError('先选择或下载官方部署工具')
        if action in ('deploy', 'promote') and c['tool'].get('version') and c['tool']['version'] != c['toolVersion']:
            raise ValueError('已下载部署程序版本 ' + c['tool']['version'] + ' 与所填 Release ' + c['toolVersion'] + ' 不一致，请重新下载或改回版本号')
        with self.lock:
            if self.running: raise ValueError('另一个任务正在运行')
            self.running = True; self.c = c
            job = uuid.uuid4().hex[:12]
            self.state.update(running=True, status='执行中', job=job, action=action, logs=[])
            self.cancel.clear(); self.logpath = self.store.root / 'logs' / (job + '.log')
        try: self.save()
        except Exception:
            with self.lock: self.running = False; self.state['running'] = False
            raise
        thread = threading.Thread(target=self._job, args=(action, selected, source), daemon=False); thread.start()
        return job
    def _job(self, action, selected, source):
        try:
            if action in ('deploy', 'checkUpdate', 'upgrade', 'distribute'):
                # Fail before touching any host when local prerequisites are missing.
                cache = ImageCache(self.store)
                for nid in selected:
                    n = self.node(nid)
                    cache.resolve(references(n['kind'], self.c['version'], action in ('deploy', 'distribute')))
            if action.startswith('database'): self.database_action(action,self.node(selected[0]),source or {})
            elif action == 'syncVersions': self.sync_versions(selected)
            elif action == 'batchUpdate': self.batch_update(selected)
            elif action == 'distribute':
                for nid in selected:
                    self.checkpoint(); self.distribute_images(self.node(nid), dependencies=True)
            elif action == 'deploy': self.deploy_all(selected)
            elif action == 'health': self.health(selected)
            elif action == 'startService': self.start_service(self.node(selected[0]))
            elif action == 'resizeJudge': self.resize_judge(self.node(selected[0]))
            elif action == 'checkUpdate': self.check_update(self.node(selected[0]))
            elif action == 'upgrade': self.upgrade(self.node(selected[0]))
            elif action == 'recoverUpdate': self.recover_update(self.node(selected[0]))
            elif action in ('adoptInspect','adopt'): self.adopt_existing(self.node(selected[0]),action=='adopt')
            elif action == 'backup': self.backup(self.node(selected[0]))
            elif action == 'promote': self.promote(self.node(selected[0]), source)
            elif action == 'rebind':
                n = self.node(selected[0])
                if n['kind'] != 'judge' or n['role'] != 'active' or not n.get('deployed'): raise ValueError('请选择已启用的评测机')
                if self.sql(self.node(n['web']), 'SELECT COUNT(*) FROM solution WHERE result < 4') != '0': raise ValueError('评测队列未清空')
                self.activate(n, rebuild=True)
            else: self.op(self.node(selected[0]), 'restoreHost')
            self.state['status'] = '完成'; self.log('任务完成')
        except Exception as e:
            self.state['status'] = '失败 / 需要检查'; self.log('失败：' + str(e))
        finally:
            try: self.save()
            except Exception as e:
                self.state['status'] = '配置保存失败 / 需要检查'
                self.log('保存部署状态失败：' + str(e))
            finally:
                with self.lock: self.running = False; self.state['running'] = False
    def database_action(self,action,n,options):
        state=self.state['nodes'].setdefault(n['id'],{})
        if action=='databaseInspect':
            state['storage']=self.op(n,'databaseInspect')
            project=next(x for x in state['storage']['locations'] if x['key']=='project')
            if n.get('prepared') and project.get('uuid'):
                binding={k:project[k] for k in ('uuid','device','mount')}
                if n.get('projectStorage') and n['projectStorage']!=binding:
                    raise RuntimeError('当前项目挂载与记录不符，未覆盖原 SSD 绑定；请检查磁盘')
                n['projectStorage']=binding;self.save()
            self.log(n['id']+'：已读取项目、数据库、题库、代码和 Docker 实际磁盘位置');return
        if action=='databaseCheck':
            state.pop('databasePlan',None)
            state['databasePlan']=self.op(n,'databaseCheck',dict(target=options.get('target','')))
            state['databasePlan']['nodeHash']=node_hash(n)
            self.log('目标检查通过；尚未停止服务或迁移数据');return
        judges=self.associated(n)
        if action=='databaseRecover':
            status=self.op(n,'databaseInspect')
            if not status['pending']:
                # Commit may have succeeded before the Windows connection was interrupted.
                managed=status.get('managedStorage')
                if managed:n['databasePath']=managed['target'];self.save()
                state['storage']=status
                self.log('没有未完成数据库迁移；已同步存储记录，未回退或停止服务');return
            self.freeze(n,judges)
            self.op(n,'databaseRollback')
            if n.get('deployed'):self.resume(n,judges)
            state['storage']=self.op(n,'databaseInspect')
            self.log('原数据库挂载及服务已恢复；新目录副本保留');return
        if not n.get('deployed') or n['role']!='active': raise ValueError('请选择已部署 / 已接管的启用 Web')
        plan=state.get('databasePlan')
        if not plan or plan['target']!=options.get('target') or plan.get('nodeHash')!=node_hash(n):
            raise ValueError('请先检查当前节点和目标目录，配置变化后须重新检查')
        latest=self.op(n,'databaseCheck',dict(target=plan['target']))
        if latest['fingerprint']!=plan['fingerprint']: raise ValueError('磁盘或容器已变化，请重新检查')
        self.client_factory(n).verify()
        if self.sql(n,'SELECT COUNT(*) FROM solution WHERE result < 4')!='0': raise ValueError('评测队列未清空，未停止服务')
        frozen=False;begun=False;committed=False
        try:
            frozen=True;self.freeze(n,judges)
            begun=True;self.op(n,'databaseBegin',dict(target=plan['target'],fingerprint=plan['fingerprint']))
            self.op(n,'databaseValidate')
            # Only DB is running here; the public application stays stopped until commit.
            self.op(n,'databaseCommit');committed=True
            n['databasePath']=plan['target'];self.save()
            self.resume(n,judges)
            state.pop('databasePlan',None)
            state['storage']=self.op(n,'databaseInspect')
            self.log('数据库迁移完成；容器、SQL 口令及管理员保持原样，原数据库文件保留')
        except Exception:
            if committed:
                self.log('新数据库目录已经提交；后续启动失败不会回退开放写入后的数据，请检查并一键启动服务')
            elif frozen:
                try:
                    self.op(n,'stop')
                    if begun:self.op(n,'databaseRollback')
                    self.resume(n,judges)
                    self.log('迁移失败，已恢复原数据库和服务；复制的目标目录保留')
                except Exception as e:self.log('恢复未完成：'+str(e)+'；请使用“恢复未完成数据库迁移”')
            raise

    def distribute_images(self, n, dependencies=False, remote=None, refs=None):
        archives = ImageCache(self.store).resolve(refs or references(n['kind'], self.c['version'], dependencies))
        for a in archives:
            self.log('校验本地镜像：'+a['name'])
            if file_hash(a['path']) != a['sha256']: raise ValueError('本地镜像文件已变化，请重新导入')
        r = remote or self.remote(n, True)
        try:
            r.op('imagePrerequisite', self.c, extra=dict(uploadBytes=max(a['size'] for a in archives)))
            for a in archives:
                self.checkpoint()
                self.log(n['id']+'：分发 '+', '.join(a['images']))
                r.op('loadImage', self.c, archive=a['path'], extra=dict(sha256=a['sha256'], images=a['images'], identities=a.get('identities',{})))
            self.log(n['id']+'：镜像分发和内容校验完成，未替换业务容器')
        finally:
            if remote is None: r.close()
    def ensure_images(self,n,dependencies=False,remote=None):
        inventory = remote.op('versionInventory',self.c) if remote else self.op(n,'versionInventory')
        available={x['ref'] for x in inventory['images']}
        missing=[ref for ref in references(n['kind'],self.c['version'],dependencies) if ref not in available]
        if missing: self.distribute_images(n,remote=remote,refs=missing)
        else: self.log(n['id']+'：直接使用服务器已有镜像，无需重新分发或外网拉取')

    def sync_versions(self,selected):
        failures=[]
        for nid in selected:
            self.checkpoint();n=self.node(nid)
            try:
                result=self.op(n,'versionInventory')
                self.state['nodes'].setdefault(nid,{}).update(inventory=result)
                self.log(nid+'：'+result['reason']+'；运行版本 '+(', '.join(result['currentVersions']) or '未运行')+'；服务器镜像 '+(', '.join(result['availableVersions']) or '无'))
            except Exception as e:
                self.state['nodes'].setdefault(nid,{}).update(inventory=dict(mode='blocked',reason=str(e),currentVersions=[],availableVersions=[],images=[],error=True))
                failures.append(nid);self.log(nid+'：同步失败：'+str(e))
        if failures: raise RuntimeError('以下节点同步失败：'+', '.join(failures))

    def batch_update(self,selected):
        # Probe every selected node before any initialization or container replacement.
        original_version=self.c.get('version','')
        try:
            self.sync_versions(selected)
            ordered=sorted((self.node(i) for i in selected),key=lambda n:n['kind']!='web')
            for n in ordered:
                inv=self.state['nodes'][n['id']]['inventory']
                if inv['mode']=='blocked' or n.get('retainedData'): raise ValueError(n['id']+'：'+inv['reason'])
                if inv['mode']=='initialize' and n.get('deployed'): raise ValueError(n['id']+'：本机曾记录已部署，但服务器配置缺失，请先核对目录；不会重新初始化')
                self.c['version']=n['targetVersion']
                if inv['mode']=='initialize':
                    if not self.c.get('tool'): raise ValueError(n['id']+'：初始化需要先选用官方部署程序')
                    if self.c['tool'].get('version') and self.c['tool']['version']!=self.c.get('toolVersion'): raise ValueError('部署程序版本记录不一致')
                required=references(n['kind'],self.c['version'],inv['mode']=='initialize')
                missing=[ref for ref in required if ref not in {x['ref'] for x in inv['images']}]
                if missing: ImageCache(self.store).resolve(missing)
            for n in ordered:
                self.checkpoint();self.c['version']=n['targetVersion']
                # Refresh classification immediately before changing this node.
                inv=self.op(n,'versionInventory')
                self.state['nodes'].setdefault(n['id'],{}).update(inventory=inv)
                if inv['mode']=='blocked': raise ValueError(n['id']+'：'+inv['reason'])
                self.log(n['id']+'：目标 '+self.c['version']+'；'+inv['reason'])
                if inv['mode']=='initialize':
                    if n.get('deployed'): raise ValueError('原配置消失，停止而非重新初始化')
                    self.prepare(n,prefer_remote=True)
                    if n['role']=='active': self.activate(n)
                else:
                    if not inv['managed'] or not n.get('prepared') or not n.get('deployed'):
                        # Adopt only after the existing runtime and credentials have passed validation.
                        prior=copy.deepcopy(n)
                        try:
                            n.pop('prepared',None)
                            self.adopt_existing(n,False);self.adopt_existing(n,True)
                        except Exception:
                            n.clear();n.update(prior);raise
                    self.upgrade(n,prefer_remote=True)
                result=self.op(n,'versionInventory')
                self.state['nodes'].setdefault(n['id'],{}).update(inventory=result)
        finally:
            self.c['version']=original_version

    def prepare(self, n, prefer_remote=False):
        self.checkpoint(); self.log(n['id'] + '：连接并准备主机')
        if n.get('retainedData'): raise ValueError('该旧站保留迁移前数据，请使用新的空目录节点，不能当作空白备用继续部署')
        tool = self.store.root / 'tools' / (self.c['tool']['sha256'] + '.bin')
        if not tool.exists(): raise ValueError('本机部署程序缺失，请重新选择文件')
        r = self.remote(n, True)
        try:
            if prefer_remote: self.ensure_images(n,dependencies=True,remote=r)
            else: self.distribute_images(n, dependencies=True, remote=r)
            result = r.op('prepare', self.c, tool=tool)
        finally: r.close()
        n['prepared'] = True; n['preparedVersion'] = self.c['version']; self.save()
        self.state['nodes'][n['id']] = dict(status='备用就绪' if n['role'] == 'standby' else '资源就绪', **result)
    def adopt_existing(self,n,commit=False):
        if n.get('prepared'): raise ValueError('节点已经托管，无需重复接管')
        if n['kind']=='judge': n['webUrl']=url(self.node(n['web']))
        prior=self.state['nodes'].get(n['id'],{}).get('adoption',{})
        if commit and (not prior.get('fingerprint') or prior.get('nodeHash')!=node_hash(n)):
            raise ValueError('请先检查接管；节点配置或容器已改变时需要重新检查')
        result=copy.deepcopy(self.op(n,'adoptInspect'))
        web=n if n['kind']=='web' else self.node(n['web'])
        self.client_factory(web).verify()
        if n['kind']=='judge':
            cli=self.client_factory(web).login()
            row=next((r for r in cli.judges() if r['user_id']==n['judgeUser']),None)
            if not row or row.get('password')!=n['judgePassword']: raise RuntimeError('Web 中原评测账号或口令不匹配')
        if commit:
            if prior.get('fingerprint')!=result['fingerprint'] or prior.get('nodeHash')!=node_hash(n):
                raise ValueError('请先检查接管；节点配置或容器已改变时需要重新检查')
            self.op(n,'adopt',dict(fingerprint=result['fingerprint']))
            n.update(prepared=True,deployed=True,adopted=True,preparedVersion=result['version'],deployedVersion=result['version'])
            self.save()
            self.log(n['id']+'：已接管，原容器、数据及口令未修改')
            self.health([n['id']])
        else:
            result.update(nodeHash=node_hash(n),status='接管检查通过；尚未写入托管标记')
            self.state['nodes'][n['id']]=dict(status=result['status'],adoption=result)
            self.log(n['id']+'：'+result['status'])
    def sql(self, web, query): return self.op(web, 'sql', dict(query=query))['value']
    def judge_account(self, n, web):
        client = self.client_factory(web).login()
        rows = client.judges(); existing = next((r for r in rows if r['user_id'] == n['judgeUser']), None)
        if existing:
            if existing.get('password') != n['judgePassword']: raise RuntimeError('评测账号已存在且口令不同，拒绝覆盖：' + n['judgeUser'])
        else:
            if self.sql(web, "SELECT count(*) FROM users WHERE user_id='" + n['judgeUser'] + "'") != '0': raise RuntimeError('该账号已是普通用户，拒绝替换')
            client.api('/admin/judger/judger_add_ajax', dict(user_id=n['judgeUser'], custom_password=n['judgePassword'], language=75, defunct=0))
        return client, existing.get('accesstime') if existing else None
    def activate(self, n, rebuild=False):
        if n['role'] != 'active': raise RuntimeError('备用节点不能执行正式部署')
        self.log(n['id'] + '：调用官方部署程序')
        if n['kind'] == 'judge':
            web = self.node(n['web']); n['webUrl'] = url(web)
            if web['role'] != 'active' or not web.get('deployed'): raise ValueError('绑定 Web 尚未成功部署')
            client, previous = self.judge_account(n, web)
            if rebuild: self.op(n, 'removeJudge')
            result = self.op(n, 'deploy')
            result['oj'] = client.heartbeat(n, previous)
        else:
            result = self.op(n, 'deploy')
            result['oj'] = self.client_factory(n).verify(initialize=True)
        n['deployed'] = True; n['deployedVersion'] = self.c['version']; self.save()
        self.state['nodes'][n['id']] = dict(status='已启用 / 检查通过', **result)
    def deploy_all(self, selected):
        for n in sorted((self.node(i) for i in selected), key=lambda n: n['kind'] != 'web'):
            if n.get('adopted'): raise ValueError('已接管节点请使用镜像更新，不重复初始化部署')
            if n.get('deployed') and n.get('deployedVersion') != self.c['version']:
                raise ValueError('已有部署升级请使用“更新所选节点”，保留现有服务和凭据')
            self.prepare(n); self.checkpoint()
            if n['role'] == 'active': self.activate(n, rebuild=n['kind'] == 'judge' and n.get('deployed', False))
    def health(self, selected, allow_stopped=False):
        failures = 0
        for nid in selected:
            self.checkpoint(); n = self.node(nid)
            result = dict(checkedAt=datetime.datetime.now().isoformat(timespec='seconds'),
                          serverStatus='SSH 不可用（连接或认证失败）', serviceStatus='未知', canStart=False)
            try:
                # Keep SSH reachability even if sudo, Docker or the application fails.
                r = self.remote(n)
                result['serverStatus'] = '在线（SSH 已连接）'; result['online'] = True
                try: result.update(r.op('health', self.c))
                finally: r.close()
                result['status'] = '服务器在线'
                containers = result.get('containers', [])
                expected = 4 if n['kind'] == 'web' else n['pods']
                complete = len(containers) == expected
                all_running = complete and all(x['running'] for x in containers)
                if n['role'] == 'standby':
                    result['status'] = '备用缓存就绪' if len(result['images']) == (4 if n['kind'] == 'web' else 1) else '在线 / 资源不完整'
                    result['serviceStatus'] = '未启动（备用）'
                    if any(x['running'] for x in containers):
                        result['status'] = result['serviceStatus'] = '异常：备用仍有运行容器'; failures += 1
                elif n.get('deployed'):
                    if not result.get('dockerRunning', True):
                        result['serviceStatus'] = 'Docker 未运行'; result['canStart'] = bool(result.get('prepared')); failures += 1
                    elif n['kind']=='judge' and result.get('totalPods',expected)>expected:
                        result['serviceStatus'] = '存在配置数量之外的评测容器，请核对 pods'; failures += 1
                    elif not complete:
                        result['serviceStatus'] = '容器缺失，请重新部署所选节点'; failures += 1
                    elif not all_running:
                        result['serviceStatus'] = '部分容器未运行' if any(x['running'] for x in containers) else '未运行'
                        result['canStart'] = bool(result.get('prepared'))
                        if allow_stopped and not any(x['running'] for x in containers):
                            result['serviceStatus'] = '已关闭'
                        else: failures += 1
                    else:
                        result['serviceStatus'] = '容器运行，业务检查中'
                        self.verify_service(n, result)
                    result['status'] = result['serviceStatus']
                else: result['serviceStatus'] = '尚未部署'
                self.state['nodes'][nid] = result; self.log(nid + '：' + result['status'])
            except Exception as e:
                failures += 1
                result.update(status='检查失败', error=redact(str(e), self.c))
                result['serviceStatus'] = '容器运行，业务检查未通过' if result['serviceStatus'] == '容器运行，业务检查中' else '未知（检测未完成）'
                self.state['nodes'][nid] = result; self.log(nid + '：' + str(e))
        if failures: raise RuntimeError(str(failures) + ' 个节点检查未通过')
    def verify_service(self, n, result):
        if n['kind'] == 'web':
            result['oj'] = self.client_factory(n).verify()
            result['serviceStatus'] = '正常（Web 登录及样式通过）'
        else:
            cli = self.client_factory(self.node(n['web'])).login()
            row = next((r for r in cli.judges() if r['user_id'] == n['judgeUser']), {})
            result['oj'] = cli.heartbeat(n, row.get('accesstime'))
            result['serviceStatus'] = '正常（评测心跳更新）'
    def start_service(self, n):
        if n['role'] != 'active' or not n.get('deployed') or not n.get('prepared'):
            raise ValueError('一键启动仅用于本工具已部署的启用节点；备用请使用“启用所选备用”')
        self.log(n['id'] + '：启动已有服务，保留原数据与配置')
        try: self.op(n, 'start')
        except Exception:
            try: self.health([n['id']])
            except Exception: pass
            raise
        self.health([n['id']])
    def associated(self, web): return [n for n in self.c['nodes'] if n['kind'] == 'judge' and n['web'] == web['id'] and n['role'] == 'active' and n.get('deployed')]
    def check_update(self,n,prefer_remote=False):
        if not n.get('prepared') or n.get('retainedData'): raise ValueError('更新只适用于本工具已管理的节点；既有手动部署不能直接接管')
        if prefer_remote: self.ensure_images(n)
        else: self.distribute_images(n)
        result=self.op(n,'updateCheck')
        result['status']='发现镜像变化，尚未替换容器' if result['updateAvailable'] else '运行镜像与目标一致'
        result['checkedAt']=datetime.datetime.now().isoformat(timespec='seconds')
        self.state['nodes'].setdefault(n['id'],{}).update(update=result)
        self.log(n['id']+'：'+result['status']);return result
    def recover_update(self,n):
        if not n.get('deployed'): raise ValueError('请选择已部署节点')
        result=self.op(n,'updateRollback')
        if not result.get('transaction'):
            self.log('没有未完成的更新');return
        if result.get('previousVersion'):
            n['deployedVersion']=n['preparedVersion']=result['previousVersion'];self.save()
        if n['kind']=='web': self.resume(n,self.associated(n))
        else: self.op(n,'start')
        self.health([n['id']])
    def upgrade(self,n,prefer_remote=False):
        target=self.check_update(n,prefer_remote=True) if prefer_remote else self.check_update(n)
        if n['role']=='standby':
            n['preparedVersion']=self.c['version'];self.save()
            self.log('备用镜像已刷新并断网自检，未启动或接入评测');return
        if not n.get('deployed'): raise ValueError('节点尚未部署，请使用批量部署')
        if not target['updateAvailable']:
            self.log('目标镜像未变化，无需停机');return
        web=n if n['kind']=='web' else self.node(n['web'])
        self.client_factory(web).verify()
        expected=self.counts(web) if n['kind']=='web' else None
        if self.sql(web,'SELECT COUNT(*) FROM solution WHERE result < 4')!='0': raise ValueError('评测队列尚未清空，未停止服务')
        judges=self.associated(n) if n['kind']=='web' else []
        original=copy.deepcopy(n); frozen=False; begun=False; committed=False
        try:
            frozen=True
            if n['kind']=='web':
                self.freeze(n,judges)
                self.snapshot(n)
            else:self.op(n,'stop')
            # begin writes a recovery journal before replacing any application container.
            begun=True; self.op(n,'updateBegin',dict(targetId=target['targetId']))
            self.op(n,'start')
            result={}; self.verify_service(n,result)
            if n['kind']=='web' and self.counts(n)!=expected: raise RuntimeError('更新前后的用户/题目/比赛/提交数量不一致')
            self.op(n,'updateCommit'); committed=True
            n['preparedVersion']=n['deployedVersion']=self.c['version'];self.save()
            for j in judges:self.op(j,'start')
            self.health([n['id']]+[j['id'] for j in judges])
            self.log(n['id']+'：更新完成，原账号、口令、挂载和基础服务已保留')
        except Exception:
            if committed:
                self.log('镜像更新已提交；后续服务检查失败，请检查日志并使用一键启动。不会自动回退已开放的站点数据。')
                raise
            if frozen:
                try:
                    if begun:self.op(n,'updateRollback')
                    n.clear();n.update(original)
                    if n['kind']=='web':self.resume(n,judges)
                    else:self.op(n,'start')
                    self.log('更新失败，已恢复原服务；备份及失败数据保留')
                except Exception as e:
                    self.log('自动恢复未完成：'+str(e)+'；请使用“恢复未完成更新”并检查日志')
            self.save();raise
    def resize_judge(self, n):
        if n['kind'] != 'judge' or not n.get('prepared'): raise ValueError('请选择本工具已准备的评测机')
        target = int(n.get('desiredPods', n['pods']))
        if not 1 <= target <= 16: raise ValueError('目标 pods 必须在 1–16 之间')
        if n.get('preparedVersion') != self.c['version'] or (n.get('deployed') and n.get('deployedVersion') != self.c['version']):
            raise ValueError('调整 pods 不同时升级版本，请先将镜像标签设回该节点当前版本')
        if target == n['pods']:
            self.log(n['id']+'：pods 数未改变'); return
        self.op(n, 'judgeResizeCheck', dict(pods=target))
        if n['role'] == 'standby':
            n['pods'] = target; self.save()
            self.state['nodes'][n['id']] = dict(status='备用目标 pods 已更新，未启动服务')
            return
        if not n.get('deployed'): raise ValueError('尚未部署，请在批量部署中配置此节点')
        web = self.node(n['web'])
        self.client_factory(web).verify()
        if self.sql(web, 'SELECT COUNT(*) FROM solution WHERE result < 4') != '0': raise ValueError('评测队列未清空，未停止原容器')
        original = copy.deepcopy(n)
        self.log(f"{n['id']}：从 {n['pods']} 调整为 {target} pods；重建此机容器，保留评测账号")
        try:
            self.op(n, 'removeJudge'); n['pods'] = target
            self.activate(n)
            self.state['nodes'][n['id']].update(serverStatus='在线（SSH 已连接）', serviceStatus=f'正常（{target} 个评测容器运行，Web 心跳已更新）')
            self.log('Web 对应评测账号心跳已更新；多个 pod 复用同一账号，不新增重复账号')
        except Exception:
            self.log('调整 pods 未完成，尝试恢复原容器数量')
            try:
                self.op(n, 'removeJudge'); n.clear(); n.update(original)
                self.activate(n); self.log('原 pods 数与 Web 心跳已恢复')
            except Exception as rollback:
                self.state['nodes'][n['id']] = dict(status='pods 调整与恢复未完成，需要检查', error=redact(str(rollback),self.c))
                self.log('恢复未完成，请检查此机容器：'+str(rollback))
            self.save(); raise
    def counts(self, web):
        return self.sql(web, 'SELECT (SELECT COUNT(*) FROM users),(SELECT COUNT(*) FROM problem),(SELECT COUNT(*) FROM contest),(SELECT COUNT(*) FROM solution)')
    def freeze(self, web, judges):
        self.log('暂停站点与关联评测机；切换期间会暂时无法访问')
        self.op(web, 'stop')
        for n in judges: self.op(n, 'stop')
    def snapshot(self, web):
        meta = self.op(web, 'snapshot', dict(user=web['user']))
        folder = self.store.root / 'backups' / (web['id'] + '-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        folder.mkdir(); restrict(folder)
        dest = folder / 'data.tar.gz'; r = self.remote(web)
        try:
            r.get(meta['path'], dest)
            if file_hash(dest) != meta['sha256']: raise RuntimeError('下载备份 SHA256 不符，远端临时快照保留')
            with r.ssh.open_sftp() as s: s.remove(meta['path'])
        finally: r.close()
        restrict(dest)
        # Secrets stay in DPAPI, the portable manifest has only recovery metadata.
        record = dict(path=str(dest), sha256=meta['sha256'], bytes=meta['bytes'], web=web['id'], version=web.get('deployedVersion',self.c['version']), site=web['site'])
        (folder / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        self.c.setdefault('backups', []).append(record); self.save()
        self.log('一致性冷备份已保存：' + str(dest)); return record
    def resume(self, web, judges):
        # Also handles a failure half-way through freeze, before judges were stopped.
        for n in judges: self.op(n, 'stop')
        self.op(web, 'start'); self.client_factory(web).verify()
        self.sql(web, 'UPDATE solution SET result=0 WHERE result IN (2,3)')
        for n in judges: self.op(n, 'start')
    def backup(self, web):
        if web['kind'] != 'web' or web['role'] != 'active' or not web.get('deployed'): raise ValueError('只能备份本工具已启用的 Web')
        judges = self.associated(web)
        if self.sql(web, 'SELECT COUNT(*) FROM solution WHERE result < 4') != '0': raise RuntimeError('评测队列未清空')
        try:
            self.freeze(web, judges); self.snapshot(web)
        finally: self.resume(web, judges)
    def promote(self, target, source_id):
        if target['role'] != 'standby': raise ValueError('仅备用节点可启用')
        if not target.get('prepared') or target.get('preparedVersion') != self.c['version']: raise ValueError('请先按当前版本准备备用镜像')
        if target['kind'] == 'judge':
            previous = copy.deepcopy(target)
            try:
                target['role'] = 'active'; self.activate(target)
            except Exception:
                self.op(target, 'stop'); target.clear(); target.update(previous); self.save(); raise
            return
        source = self.node(source_id) if source_id else None
        if not source or source['kind'] != 'web' or source['role'] != 'active' or not source.get('deployed'): raise ValueError('请选择本工具管理且在线的主 Web，迁移数据库和题库')
        if source['host'] == target['host']: raise ValueError('备用 Web 切换要求不同主机')
        if source.get('deployedVersion') != self.c['version']: raise ValueError('迁移不能同时升级版本；先保持源站版本')
        judges = self.associated(source); original_target = copy.deepcopy(target); original_source = copy.deepcopy(source)
        original_judges = {n['id']: copy.deepcopy(n) for n in judges}
        target_health = self.op(target, 'health')
        source_health = self.op(source, 'health')
        capacity = self.op(target, 'migrationCheck')
        source_capacity = self.op(source, 'migrationCheck', dict(source=True))
        size = source_capacity.get('dataBytes', 0)
        required = int(size * 1.1) + 100 * 1024**2
        if size and (source_capacity['freeBytes'] < required or capacity['freeBytes'] < required + size or shutil.disk_usage(self.store.root).free < required):
            raise ValueError('冷迁移空间不足：源机/Windows 需容纳归档，目标机需同时容纳归档及解压数据')
        for container in source_health['containers']:
            if target_health['imageIds'].get(container['image']) != container['imageId']:
                raise ValueError('源站容器与备用缓存镜像不一致：' + container['image'] + '；迁移不同时升级镜像')
        self.client_factory(source).verify()
        expected = self.counts(source)
        if self.sql(source, 'SELECT COUNT(*) FROM solution WHERE result < 4') != '0':
            raise RuntimeError('仍有待评测或评测中的提交，请等队列清空后再迁移')
        started = False
        try:
            started = True; self.freeze(source, judges)
            snapshot = self.snapshot(source)
            # Keep site/database identity and secrets, but change host/SSH/project directory.
            for key in ('site', 'secrets', 'adminUser'): target[key] = copy.deepcopy(source[key])
            r = self.remote(target)
            try:
                remote_archive = target['path'] + '/.migration.tar.gz'
                # Project root is root-owned; upload to the SSH user's home then let root restore.
                with r.ssh.open_sftp() as s: remote_archive = s.normalize('.') + '/.csg-migration-' + uuid.uuid4().hex + '.tar.gz'
                r.put(snapshot['path'], remote_archive)
                with r.ssh.open_sftp() as s: s.chmod(remote_archive, 0o600)
                try: r.op('restore', self.c, extra=dict(archive=remote_archive, sha256=snapshot['sha256']))
                finally:
                    with r.ssh.open_sftp() as s: s.remove(remote_archive)
            finally: r.close()
            target['role'] = 'active'
            self.op(target, 'deploy'); self.client_factory(target).verify(initialize=False)
            if self.counts(target) != expected: raise RuntimeError('迁移前后的用户/题目/比赛/提交数量不一致')
            self.sql(target, 'UPDATE solution SET result=0 WHERE result IN (2,3)')
            for n in judges:
                n['web'] = target['id']; self.activate(n, rebuild=True)
            target['deployed'] = True; target['deployedVersion'] = self.c['version']; source['role'] = 'standby'; source['deployed'] = False
            source['retainedData'] = True; self.save()
            self.state['nodes'][source['id']] = dict(status='已停止，旧数据保留')
            self.state['nodes'][target['id']] = dict(status='迁移成功 / 主站已切换')
            self.log('新 Web 已验收，关联评测机全部切换；旧站保持停止，原数据和 Windows 快照保留')
        except Exception:
            if started:
                self.log('迁移未完成，开始恢复原 Web 与评测机')
                try:
                    self.op(target, 'stop')
                    source.clear(); source.update(original_source)
                    for n in judges: self.op(n, 'stop')
                    self.op(source, 'start'); self.client_factory(source).verify()
                    self.sql(source, 'UPDATE solution SET result=0 WHERE result IN (2,3)')
                    for n in judges:
                        nid = n['id']; n.clear(); n.update(original_judges[nid]); self.activate(n, rebuild=True)
                    self.log('原站和评测机已恢复；目标迁移文件保留，重试前需另选空目录')
                except Exception as rollback:
                    self.log('恢复未完成，需要依据日志手动处理：' + str(rollback))
            target.clear(); target.update(original_target); self.save(); raise

def node_hash(node):
    return hashlib.sha256(json.dumps(node,sort_keys=True).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''): h.update(part)
    return h.hexdigest()
