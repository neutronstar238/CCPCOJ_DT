# v1.14.2：Windows / WSL 首次 SSH 主机信任修复

## 问题与定位

在 Windows DT 中使用私钥向首次连接的 Linux 服务端点击「仅推送所选版本」，Ansible 在 `Create deployment directory` 报 `stdin is not interactive`，recap 为 `ok=0 changed=0 unreachable=1`。

`arena_deploy.py` 的 `CONTROLLER` 通过标准输入接收 JSON，关闭输入后启动 Ansible；它开启主机密钥验证，却没有把 Windows 的信任记录传给 WSL。Ansible 2.19 的 `paramiko_ssh.MyAddPolicy.missing_host_key()` 对未知公钥调用 `display.prompt_until()`；后台进程没有 TTY，因而失败。这不是选手机账号错误，也不是私钥解锁提示。

## 修改

- `arena_ssh.py`：先做不登录、不执行命令的 SSH 握手；验证 Windows 信任记录和工作台已固定的公钥；未知公钥交给页面确认，冲突公钥拒绝且不覆盖。
- `arena_deploy.py`：保存待确认任务；确认与任务 ID、目标配置绑定。把确切公钥传入任务私有 HOME，供 WSL Paramiko 使用；OpenSSH 同样指定严格验证和独立 known_hosts。任务完成清理，不修改 WSL 用户文件。
- `web/arena.js`、`server.py`：增加首次指纹展示、确认并继续原任务的入口。

## 验证

39 项相关自动测试通过：管控配置、缓存、接口、版本、扫描、私钥认证及新增信任回归；新增回归涵盖首次信任、端口隔离、复用 Windows 记录、指纹变化拒绝、旧任务拒绝、配置变化拒绝、确认前不传私钥且不启动 Ansible。

WSL 实际控制器 smoke 通过；六组服务端／选手端 action playbook 的 Linux／Windows 语法检查通过；PowerShell 安装脚本解析通过。

通过内置浏览器操作打包版 v1.14.2：原目标首次指纹展示正常，确认后完成真实内网上传，Ansible `ok=3 changed=2 unreachable=0 failed=0`。选手端页面预览为 334 台，所有 endpoint 与配置服务端一致，两台此前认证失败机器仍排除。

本轮执行「仅推送所选版本」，只上传服务端 v1.11.0 到暂存目录；没有安装／启动服务端或部署选手端。
