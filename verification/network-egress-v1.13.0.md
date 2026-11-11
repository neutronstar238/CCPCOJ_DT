# v1.13.0 网络出口验证

范围：Windows DT 扫描、原 OJ SSH 分发、WSL Ansible 管控推送／部署／状态连接的动态有线优先。不改 OJ 和管控软件，不修改系统路由，不向赛场主机部署。

通过的检查：

- 8 项新增网络回归测试：有线优先于 Wi-Fi metric、中文／任意接口名、DHCP 地址更新、断线与虚拟网卡回退、更具体路由保留、多地址选源、接口与源地址绑定、读取失败明确报错、扫描 TCP/ICMP 共用出口、128 KiB 全字节 stdio 传输、代理目标白名单与原地址／证书策略保留。
- 192 项 Python 测试通过；11 项旧 SSH 管理测试独立运行通过。完整套件同进程运行时旧 SSH 授权夹具出现断连失败，独立运行通过；未把该失败计为完整套件通过。SSH 配置测试使用测试进程内 FQDN mock 隔离本机 DNS 等待，不修改生产 DNS。
- 31 项 JavaScript 测试通过。
- 真实浏览器扫描流程通过，显示模拟有线接口与源地址；进度、取消、任务互锁、扫描 IP 导入、统一／独立凭据、加密持久化均通过。网络探测使用夹具，不扫描真实赛场网段。
- 实际 Windows 当前活动网卡读取成功；绑定当次地址和接口到本机监听器的 TCP 连接通过；带源 IPv4 的 ICMP 到本机地址通过。此项不使用固定 LAN IP。
- 实际 WSL → Windows stdio 连接器 → 本地 SSH 夹具通过，使用已登记主机指纹；实际 Ansible controller 的 raw 任务通过。
- 实际 WSL pywinrm Transport → 临时 CONNECT 代理 → Windows 连接器 → 本地 HTTPS 夹具通过，校验证书及目标地址，128 KiB 全字节响应一致。
- 六种角色／动作 playbook 的 Ansible 语法、私有 inventory 权限／清理、Windows 安装脚本 PowerShell 语法通过。
- PyInstaller EXE 通过静态页面／API、DPAPI 凭据库、本地缓存校验和复用检查；同一 EXE 作为 WSL 的 Windows 连接器，SSH、完整 Ansible raw 任务和 HTTPS 二进制传输均通过。发布包脱敏检查通过。

边界：当前机器没有同时在线的有线和 Wi-Fi 供实网 A/B；此分支用路由夹具验证。未宣称完成真实赛场 Windows WinRM 登录／安装和 Linux 多机分发验收。接口有路由不代表目标必定可达；任务中途拔线需恢复后重试。下载仍沿用正常外网路由，选手机安装后的自身上报路由不由 DT 修改。
