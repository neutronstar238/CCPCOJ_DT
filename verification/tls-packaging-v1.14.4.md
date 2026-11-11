# v1.14.4：Release 查询与 HTTPS 下载打包修复

## 复现与原因

在 Windows 打包版点击「管控服务端 → 扫描全部 Releases」，立即出现 `[Errno 2] No such file or directory`；相同源码直接运行能成功查询。增加不含路径和凭据的异常位置后，定位为 `FileNotFoundError（ssl.py:440）`。

构建使用的 Python 运行时会在 `SSLContext.__new__` 加载与 ssl 模块相邻的 `codex-rejected-cas.pem` 证书策略文件。PyInstaller 未自动收集此数据文件，导致所有依赖该上下文的 HTTPS 操作在联网前失败。

## 修复与验证

- `build.py` 收集运行时确实存在的证书策略文件，保留原有校验策略。
- 打包完成后运行真实 EXE 的 `--self-check-tls`；必须成功构造启用主机名验证、`CERT_REQUIRED` 的上下文才能生成发布包。
- Release 查询错误补充异常类别、源码文件名和行号，不显示本机完整路径、凭据或命令参数。
- 9 项 Release 回归测试通过；真实打包 EXE TLS 自检通过。
- 内置浏览器中扫描成功，发现两个可用 Release；选定 v1.12.0-rc.2，四种产物及 SHA256 检查通过。
- 首次 GitHub CLI 下载临时失败，同一产物检查及页面重试成功；远端部署前完成 SHA256 校验。实际服务端升级 recap 为 `ok=4 changed=2 unreachable=0 failed=0`，服务运行及端口监听均为 true。

本修复针对 DT 自身的 HTTPS 运行环境，不改变 CSGArena 服务端证书、管理账号或远程管理权限。
