# 扫描、推送的有线优先

Windows DT 默认启用，无需填写本机 IP，也不需要管理员权限。每次扫描、赛场管控推送／部署／状态查询开始时读取网卡、有效 IPv4 地址和路由；原有 OJ SSH 分发在建立连接时读取。网卡名称不限语言，不按“以太网”字符串判断。

选择规则：先取匹配目标的最长路由前缀，再优先物理有线接口，之后比较路由与接口 metric。接口需已连接，地址需可用于发送；虚拟网卡不计为物理有线。有多个 IPv4 时优先与目标同子网的地址。没有适用的有线接口则使用目标路由对应的其他接口，并显示“按目标路由回退”。这保留了跨网段、VPN 等更具体的路由；有线默认网关不会覆盖 Wi-Fi 上更具体的目标子网。路由存在并不保证目标一定可达。

扫描状态显示接口名称、当次源 IPv4、目标路由。推送任务日志显示所用出口。读取网卡失败会停止并提示，不会假报使用了有线。任务期间拔线或地址改变可能导致当前连接失败；恢复网络后重新执行，会重新识别地址。不会修改 Windows 全局路由或网卡优先级，也不会关闭 Wi-Fi。

TCP 连接同时绑定源地址和 Windows `IP_UNICAST_IF`；ICMP 使用带源 IPv4 的 `IcmpSendEcho2Ex`。网卡索引与地址仅在当次内存任务中使用，不保存到配置。下载 GitHub Release／安装包沿用本机正常外网连接；此策略针对向目标机器的扫描和管理连接。

DT 运行在 Windows，Ansible 仍在 WSL。SSH 使用 Paramiko ProxyCommand，经 WSL interop 启动 Windows 连接器；WinRM HTTPS 使用仅监听 WSL 回环地址的临时 CONNECT 代理，再调用同一个连接器。实际出站 TCP 由 Windows 建立，因此 WSL NAT 不会另选 Wi-Fi。需要 WSL 的 Windows 程序互操作可用，以及默认 `/mnt/<盘符>` 挂载（与现有本地缓存要求一致）。不需要对局域网开放转发端口。

代理只允许该任务的目标 IP 和管理端口，退出任务后关闭。SSH 主机地址和指纹校验、WinRM 原目标地址和 HTTPS 证书校验保持有效，代理不解密业务流量。登录密码仍通过私有 WSL inventory 传入，不出现在连接器命令参数中。

该策略控制 DT 到目标机器的连接。选手机安装后到赛场服务端的连接使用选手机自身网络路由，DT 不修改远端网卡配置。

实现依据：[Microsoft IP_UNICAST_IF](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options)、[pywinrm proxy 参数](https://github.com/diyan/pywinrm/blob/master/winrm/transport.py)。
