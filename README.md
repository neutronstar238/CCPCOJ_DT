<p align="center">
  <strong>CCPCOJ_DT</strong><br>
  竞赛运维的两套程序
</p>

<p align="center">
  <b>CSGOJ 部署工作台</b> 的源码在本仓库。<br>
  <b>赛场管控</b> 闭源，只以单文件二进制放在 Release 里。
</p>

<p align="center">
  <a href="https://github.com/neutronstar238/CCPCOJ_DT/releases/latest"><strong>下载最新 Release</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/RELEASE-v1.10.3.md">工作台发布说明</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/SZTU-ACM/CCPCOJ-2">上游 OJ / Linux 部署程序</a>
</p>

---

## 两套工具

| | CSGOJ 部署工作台 | 赛场管控（CSGArena） |
| --- | --- | --- |
| 公开形式 | 源码、文档，以及 Windows 完整包 | 闭源。每一端一个二进制，源码不进入本仓库 |
| 当前附件 | 工作台 **v1.10.3** | Windows **0.4.0-dev.20261023.6** · Linux **0.4.0-dev.20261027.1** |
| 用途 | 在 Windows 上准备镜像，部署或更新远端与本机 WSL 的 Web、评测机 | 选手机连接中央服务端；控制、文件与媒体经中央转发 |
| 平台 | 管理端 Windows x64；服务器面向 Ubuntu 24.04 amd64 | Windows 与 Linux，均为 amd64 / x64 |

> [!NOTE]
> 工作台、场控程序、OJ 镜像标签、上游 Linux 部署程序使用彼此独立的版本号。它们出现在同一个 Release 页面，只是为了放在一处下载。

---

## 下载

附件都在 [v1.10.3](https://github.com/neutronstar238/CCPCOJ_DT/releases/tag/v1.10.3)。GitHub 自动附带的 Source code 只有工作台源码：没有打好的 Windows 程序，也没有场控二进制。

### 部署工作台

| 文件 | 内容 |
| --- | --- |
| [CSGOJ-Deployment-Console-Windows-v1.10.3.zip](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/CSGOJ-Deployment-Console-Windows-v1.10.3.zip) | Windows x64 完整包，约 15.1 MB |
| [CSGOJ-Deployment-Console-Windows-v1.10.3.zip.sha256](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/CSGOJ-Deployment-Console-Windows-v1.10.3.zip.sha256) | 上面这个 ZIP 的 SHA256 |

在下载目录核对第一列：

```powershell
Get-FileHash -Algorithm SHA256 .\CSGOJ-Deployment-Console-Windows-v1.10.3.zip
Get-Content .\CSGOJ-Deployment-Console-Windows-v1.10.3.zip.sha256
```

摘要一致后解压整个目录，双击 `启动部署工作台.cmd`，保持程序运行。浏览器打开的页面只监听 `127.0.0.1`。正式包自带 Python 运行时和 SSH 组件，管理电脑不必再装 Python、Node.js、Ansible 或 PuTTY。

关闭网页后，后台进程仍可能继续运行。替换或删除程序目录前先退出；若系统提示目录被占用，检查该程序以及工作目录指向它的终端。退出工作台不会停止已经部署的远端或 WSL 业务。

完整包里有程序、网页、节点模板、源码、操作文档、文字验证记录、逐文件 SHA256 和第三方依赖许可。OJ 镜像、上游 Linux 部署程序、个人配置、私钥、口令导出和验收截图需要另行准备或留在本机。

### 赛场管控

四个单文件，于 2026-10-27 补进上述 Release。附件里没有场控源码、测试数据、密码、证书或虚拟机。

| 文件 | 角色 | 程序版本 | 大小 |
| --- | --- | --- | --- |
| [csgarena_server_windows_amd64.exe](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/csgarena_server_windows_amd64.exe) | Windows 中央服务端 | `0.4.0-dev.20261023.6` | 16.3 MB |
| [csgarena_client_windows_amd64.exe](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/csgarena_client_windows_amd64.exe) | Windows 选手机 | `0.4.0-dev.20261023.6` | 51.5 MB |
| [csgarena_server_linux_amd64](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/csgarena_server_linux_amd64) | Linux 中央服务端 | `0.4.0-dev.20261027.1` | 23.9 MB |
| [csgarena_client_linux_amd64](https://github.com/neutronstar238/CCPCOJ_DT/releases/download/v1.10.3/csgarena_client_linux_amd64) | Linux 选手机 | `0.4.0-dev.20261027.1` | 23.3 MB |

Linux 客户端包含随后修复的 X11 中文霸屏显示。Windows 与 Linux 的构建号不同，控制协议没有因此改变。部署某一平台时，同时换上该平台的服务端和客户端，并保持机器时钟同步。Linux 下载后为文件加上执行权限：

```bash
chmod +x csgarena_server_linux_amd64 csgarena_client_linux_amd64
```

「单文件」指每一端只分发一个程序。运行后仍会在本机写下配置、证书、恢复记录，以及内嵌媒体运行时需要的文件。

<details>
<summary>场控附件 SHA256</summary>

```text
b6356896bb2b854a23c5b4a036cb60bb6cdc0fd5c4b8a9de9ccea95c27581310  csgarena_server_windows_amd64.exe
c6dc8dea217f06db3406b7a9a94443bdbd1eae2a0b31c4a99518edb9ddb710aa  csgarena_client_windows_amd64.exe
8991db7dec723e6fe949a684fe50750906c451840d7c43c57c481922cb69067e  csgarena_server_linux_amd64
af13b5753ff203353c42df1ca08aec2ec2681a69aa08f489b245909760553457  csgarena_client_linux_amd64
```

</details>

这批程序仍是 **dev 测试版**。Windows 附件保持隔离虚拟机验收时的原始字节。

| 已核对 | 尚未纳入本次验收 |
| --- | --- |
| Windows：屏幕，虚拟摄像头 / 麦克风采集，键鼠远控；考试期间阻断 `shutdown /l`（含 32 位及复制改名）；退出考试后恢复原策略并实际注销 | Windows 物理摄像头 / 麦克风、SRT 导播接收、管理网页的浏览器观感 |
| Linux X11：中文通知、中英混排、换行与中文错误提示；在 640×480、1280×720、4K 独立 Xvfb 上核对像素，取消后恢复原画面和键鼠 | 物理显卡、多屏、Wayland |

Windows 上的注销限制要求系统已强制执行 AppLocker，且 AppIDSvc 正在运行；条件不满足时会明确失败，并保持该受保护服务原样。自编的强制注销程序、管理员手动操作和物理断电不在这道限制之内。

Windows 客户端内嵌 [FFmpeg 9.0.2](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2)（[对应源码](https://github.com/FFmpeg/FFmpeg/commit/946fcce07b)，[GPLv3](https://www.gnu.org/licenses/gpl-3.0.html)）。Linux 客户端内嵌 [Noto Sans SC](https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf)（[SIL OFL 1.1](https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/LICENSE)），选手机不必再安装中文字体或配置中文 locale。

---

## 部署工作台

侧栏按这个顺序组织：

| 页面 | 做什么 |
| --- | --- |
| SSH 密钥管理 | 单独维护连接；生成或复用 Ed25519 密钥，配置公钥，测试免密登录；扫描已有 `web` / `pcj` 前缀密钥；同步本机 SSH 别名 |
| 节点配置 | 添加 Web / 评测机：IP、账号、提权、目录、启用或备用、绑定 Web、pods；预览并导入 XLSX；主机图形界面、休眠和性能设置 |
| 版本与镜像分发 | 查询上游 Release 与镜像标签，下载或导入部署程序和镜像，识别本机已有镜像，管理缓存并做内网分发 |
| 批量更新 | 读取各机运行版本和已有镜像，逐台指定目标；可全选 PCJ 或 Web；区分已有部署更新与首次初始化 |
| 状态与主备切换 | 检测 SSH、业务服务和实际运行 pods；启动服务、调整 pods、更新 Web 地址、备份、受控主备迁移、数据库迁移 |
| 部署资料 | 查看资料；按节点或全部导出管理员、评测账号和数据库业务口令 |
| 本地部署 | 选择 WSL 发行版和 Linux 路径，部署或更新本地 Web / PCJ，查询状态，开启或关闭所选服务 |
| 使用说明 | 在工具内阅读操作帮助 |

普通配置在输入完成、失焦或变更选项后自动保存，界面给出结果。部署、更新、迁移、确认导入、删除缓存需要主动执行。保存配置本身不改变正在运行的服务器。

### 环境

**Windows 管理电脑。** 使用 Windows x64。在线查询版本和镜像时，需要能访问 GitHub 与所选 Registry。镜像层、归档和备份按实际体积占用磁盘。下载、导入归档和内网分发不要求安装 Docker Desktop；只有读取或导出本机已有 Docker 镜像时，才需要可用的 Docker Linux 引擎。

**远程服务器。** 主要面向 Ubuntu 24.04 LTS、Linux amd64。事先准备已存在的 SSH 账号，以及 sudo / root 提权方式。机器上还没有 Docker 时，可以在初始化阶段安装；apt 软件包仍要从网络取得。镜像能在内网分发，不代表一台空系统可以完全离线装完。已有部署要使用正确的项目目录和凭据。数据库迁移使用事先挂载好的本地磁盘和 rsync，不格式化磁盘。

**本机 WSL。** 需要 WSL 2 Ubuntu 24.04 x86_64、systemd，以及该发行版自己的 Docker。保持 Docker Desktop 对该发行版的集成关闭。工作台会检查 Docker 所在环境，避免把 WSL 数据路径交给另一套文件系统。

### 第一次远程部署

1. **SSH 密钥管理。** 添加连接，填写 IP、SSH 用户、端口和私钥名称。生成或复用密钥，写入服务器公钥，并验证纯密钥登录。SSH 账号需要已经存在于服务器上。
2. **节点配置。** 添加 Web 和评测机，填写项目目录、提权方式和必要凭据。评测机绑定对应 Web 并设置 pods。备用节点只准备资源。
3. **版本与镜像分发。** 选择已发布的 Linux amd64 部署程序，或上传本地文件；再选择实际存在的 OJ 镜像标签并准备归档。版本默认留空，不自动挑最新版。
4. **首次 Web 初始化** 还要准备 MySQL、Nginx、phpMyAdmin 三项依赖镜像。只更新程序时，默认不下载这三项。标签以界面和所选部署程序的兼容要求为准。
5. 可以先把镜像分发到勾选的节点。分发负责上传、校验和 `docker load`。
6. 打开 **批量更新**，同步服务器上的版本与镜像，选择节点和目标版本，核对维护确认后执行。Web 先于评测机。
7. 在状态页检测。Web 核对登录和样式资源；评测机核对容器和新心跳，并显示检测当时实际运行的 pods。

已有完整部署按原配置更新。尚未托管的完整部署先核对原凭据，再接管。只有确认过的空白节点才做初始化。残留数据、缺失容器、异常挂载、未完成事务或检测失败会停在原地，等待处理。

某一台失败后，本批停止。已经成功的节点保持新状态。已有 Web 更新替换 OJ 应用容器，并保留数据库等基础容器、业务数据、口令和运行参数；评测机保留账号、pods 和绑定关系。请在维护时段执行，并另行保留业务备份。

### 版本、缓存与分发

查询官方仓库的公开 Release，包括已发布的 `exp` 标签，再选择该 Release 的 Linux amd64 附件。镜像列表来自上游接口，输入框也可以手填；标签是否存在，以实际请求为准。指定版本缺失时保持缺失，不悄悄换成另一个版本。

镜像优先源可以是腾讯云、Docker Hub 或自定义 HTTPS 源。腾讯云优先时，失败后再试 Hub；Hub 优先时顺序相反；自定义源失败后，再试腾讯云和 Hub。日志会写出每次尝试的结果。工具通过 Registry V2 下载 Linux amd64 内容，最多四层并行，核对清单、压缩层摘要和解压层 diff_id，然后生成标准 Docker-save 归档。同名标签在不同仓库里的内容可能不同，工具记录实际镜像身份，并在分发后核验。

已校验的层、已有归档，以及可用的本机 Docker 缓存可以复用。下载仍会读取远端清单，避免同名 `exp` 标签更新后继续用旧内容。代理候选要先通过 HTTP CONNECT 检查，并排除 53000 控制端口。工具不修改系统代理或 Docker 的永久配置。

本地已有文件时：

- 上传 Docker-save 格式的 tar。工具按内容读取真实标签、平台和镜像身份。
- 「识别已有本地镜像」检查工具缓存、常用本地目录，以及正在运行的本机 Docker。它不连接远程 Docker，也不自动启动 Docker。
- 选中归档后，采用其真实版本和文件路径。选中 Docker 镜像时，可以先导出到工具缓存再分发。
- 同一版本有多个候选时，需要明确选择其中一个。没有唯一匹配时，工具不擅自选用。
- 切换缓存版本会清掉不匹配的旧路径。删除缓存需要二次确认，只删除工具管理的归档和相关选用记录。

默认下载、导入和「刷新本地缓存」共用 `%LOCALAPPDATA%\CSGOJDeploy\images` 及其索引。使用 `--data-dir` 时，以该资料目录为准。还在下载的层缓存，还不是可以分发的完整归档。

分发优先使用所选的本地归档。批量更新优先使用服务器上已经存在的目标镜像，缺少时才从本机上传。分发过程核对归档 SHA256、配置摘要、文件系统层和平台。服务端部署使用 `--no-pull`，缺少的 OJ 镜像不会由部署程序自己去外网拉取。

### 连接与节点表

私钥保存在 Windows。工具只向该用户的 `authorized_keys` 追加公钥。SSH 登录成功之后，sudo 仍要单独配置提权密码。

修改 IP、用户名或端口时可以保留原来的私钥名。保存后，使用该私钥名的已保存节点，以及本工具管理的 SSH 别名，会一起更新，随后可以使用 `ssh 别名`。换了服务器或登录用户时，确认新目标已经授权对应公钥。主机公钥变化时，连接会停下，旧指纹保留。别名与其他密钥冲突时，原配置保留并给出提示。

扫描已有 `web` / `pcj` 前缀密钥后，先预览再选择导入。同名但内容不同的私钥不会被覆盖。

`templates/CSGOJ节点导入模板.xlsx` 用来批量填写节点和全局设置。先预览，再确认导入；按节点名称合并，表里没有的已有节点保留。导入不会自动连接或部署。

- 示例 IP 留空，填入自己的地址。
- `备用(0/1)`：`0` 启用，`1` 备用。
- 密码可以留空，稍后在界面补上。写进 Excel 的密码是明文。
- 项目目录默认 `/home/ccpc00/cpc`，准备前可以修改。同一台机器上的不同实例使用各自的目录。
- 未选择版本时保持留空。普通配置自动保存；保存失败的值不会被当成已经部署。
- 已托管节点的身份、目录和业务口令另有保护，不能靠普通表格导入覆盖。

### 状态、备份与数据盘

SSH 不可用表示连接或认证失败。服务状态单独显示容器和业务检查。PCJ 的 pods 来自最近一次检测，显示的是当时实际在跑的数量。

已有容器停着时，可以一键开启；容器本身缺失时，回到部署检查。调整 pods 时，先在节点里设定目标数量，再在维护时段应用。工具会检查资源与队列，并在失败后尝试恢复原数量。心跳正常、容器数相符，还不等于每个 pod 都用真实提交验证过。

Web 备份与主备切换在受控停机下进行：暂停相关服务，把完整数据保存到 Windows，核对 SHA256，再恢复或切换，并检查登录、样式和关键数据数量。原始数据与快照都保留。这是冷备份和受控切换，详细步骤见 [迁移与恢复](docs/03-迁移与恢复.md)。切换完成后，旧站保持停止。

数据库目录：

- 首次初始化可以把数据放到已挂载磁盘上的空目录；留空则用项目默认路径。
- 已部署节点先「检测实际存储位置」，查看项目、数据库、代码、题库和 Docker 的设备、挂载与空间。
- 迁移需要空目标目录、维护确认和二次确认。相关服务和 MySQL 停止后，用 rsync 保留属性并校验，再以 bind 挂载保持官方数据路径。
- 工具记录磁盘 UUID 并检查挂载，避免磁盘未挂上时把数据写进系统盘。Docker 的挂载依赖可能影响同一台机器上其他容器的开机启动。
- 原数据保留。未完成的事务从恢复入口继续。工具不格式化磁盘，不迁移全部 Docker 镜像，也不擅自删除残留副本。

### 本机 WSL

1. 选择已经安装的 WSL 发行版，填写 Linux 根目录，默认 `/srv/csgoj-local`。Web 和评测机使用各自的子目录。
2. 复用同一套 Windows 部署程序和镜像缓存，配置本地 Web 端口、管理员、PCJ pods 和目标版本。
3. PCJ 可以连接本地 Web，也可以使用外部 Web 地址和已有评测账号。外部模式不修改外部站点，也不替它注册账号。
4. 勾选 Web、PCJ 或两者，确认后部署或更新。已有实例保留数据和配置。
5. 「获取本地版本与状态」读取实际版本和容器状态。「一键开启所选服务」恢复已有服务。
6. 「关闭所选服务」只停止所选容器，保留容器、数据、配置和镜像。

页面上的路径是发行版内部的 Linux 路径。Windows 上 WSL 虚拟磁盘的位置由 WSL 自己管理。要求见 [本地部署说明](docs/10-本地部署-WSL.md)。

### 本机资料与口令

默认资料目录是 `%LOCALAPPDATA%\CSGOJDeploy`，与解压后的程序目录分开：

```text
config.dpapi            节点配置与业务口令
ssh-connections.dpapi   独立 SSH 连接
local-deploy.dpapi      本地 WSL 部署配置
known_hosts             已登记的主机公钥
keys/                   SSH 私钥与公钥
tools/                  Linux 部署程序缓存
images/                 镜像归档、已校验层和索引
logs/                   执行日志
backups/                冷备份和校验清单
```

口令配置用当前 Windows 用户的 DPAPI 加密。不要直接改这些加密文件；它们也不是换一个 Windows 用户就能打开的备份。私钥、快照和导出的业务资料另外保管。

「部署资料」可以按勾选节点或全部节点导出 TXT。其中的管理员、评测账号、MySQL root、数据库业务和 phpMyAdmin 口令来自本机保存的记录。导出不改服务器密码；若后来在站点里改过密码，本机旧记录可能已经失效。导出文件含明文口令，不要放进公开仓库。

### 遇到问题时

| 现象 | 先看哪里 |
| --- | --- |
| `ssh 别名` 仍要求密码 | 本机 SSH config 是否已同步，User / HostName 是否正确，新用户是否已写入对应公钥 |
| sudo 失败 | 登录密码和提权密码是否一致，账号有没有 sudo；公钥登录不会带上 sudo 密码 |
| 在线版本查询超时 | 本机网络、代理，以及 GitHub / Hub 是否可达；已缓存的归档分发不必先刷新在线列表 |
| 优先源失败后出现 Hub 日志 | 按镜像源回退的正常过程；看日志里最终成功的源和标签 |
| 分发完成，服务版本没变 | 分发不部署；到批量更新里选择目标版本再执行 |
| 磁盘、数据或挂载异常 | 先看存储位置和未完成事务，保留现场 |
| 目录删不掉或替换不了 | 退出对应程序，以及占用该目录的终端；资料目录单独保留 |
| 本地 WSL 数据路径异常 | 核对所选发行版，以及 Docker Desktop 集成是否关闭；使用该发行版自己的 Docker |

---

## 从源码运行

仓库根目录就是工作台源码。场控程序没有对应源码树。Windows 上使用 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe server.py
```

独立的测试资料目录：

```powershell
.\.venv\Scripts\python.exe server.py --no-browser --data-dir .\state
```

回归测试里，JavaScript 部分需要 Node.js 和 npm：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
npm install
npm test
```

构建 Windows 完整包：

```powershell
.\.venv\Scripts\python.exe build.py
```

产物写到 `dist/releases/`：ZIP、`.zip.sha256` 和 `.zip.privacy.json`。包内另有 `SHA256SUMS.txt` 和第三方依赖说明。脚本只收录允许发布的源码和文档；验收目录只收 Markdown。脱敏检查失败时会报错，这时不要发布刚生成的候选 ZIP。

构建阶段目录已经存在时，脚本会停下来，避免盖掉旧产物。先把该版本的 `dist/release-v...` 改名留档，再重新构建。`tests` 里的 `*_check.py` 是额外环境检查，部分会创建实际服务或改动测试环境。日常回归用上面的 `test_*.py` / `*.cjs`，不要对生产配置把全部脚本跑一遍。

---

## 文档

- [连接与准备](docs/01-连接与准备.md)
- [Web 与评测机部署](docs/02-部署与验收.md)
- [迁移与恢复](docs/03-迁移与恢复.md)
- [离线与排障](docs/04-离线与排障.md)
- [验证记录](docs/05-验证记录.md)
- [镜像更新与恢复](docs/06-镜像更新.md)
- [批量更新](docs/09-批量更新-v1.8.0.md)
- [版本下载与内网分发](docs/09-版本下载与内网分发.md)
- [本地部署 WSL](docs/10-本地部署-WSL.md)
- [本地镜像识别与界面修复](docs/11-本地镜像识别与界面修复.md)

部分详细文档仍保留功能第一次加入时的版本记录。当前功能入口以本页为准。第三方组件遵循各自许可，工作台发布包附有许可说明。上游 OJ、上游部署程序和赛场管控二进制都不随工作台源码树分发；场控二进制只出现在 Release 附件中。
