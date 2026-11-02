# Ge 启动器

一个只给几个朋友用的极简 Minecraft: Java Edition 启动器。Python 3.11+ / CustomTkinter（`pip install -r requirements.txt`），不需要编译。

支持原版 / Fabric / Forge / NeoForge，支持离线账号（微软账号等待 Mojang 审批，暂不可用），能直接接管 HMCL、PCL2、官方启动器已经装好的 `.minecraft`。

设计思路见 [简化启动器方案.md](简化启动器方案.md)。

## 功能

- **启动**：解析版本 JSON、合并 `inheritsFrom`、按规则过滤库、解压 natives、拼 `java` 命令行
- **实例即文件夹**：`versions/<实例名>/`，游戏版本作为参数记在 JSON 的 `clientVersion` 里；实例名重复会阻止安装（不再覆盖）
- **安装原版**：官方 version_manifest → 版本 JSON → 库 / assets / 客户端 jar，全程 sha1 校验，多源自动切换
- **安装加载器**（Fabric / Forge / NeoForge，全部装进现有实例，和 HMCL 的 patch 一样）：
  - 点「装 Fabric / Forge / NeoForge」会先弹出该实例可用的加载器版本列表，选版本再装
  - Forge / NeoForge 走 processors 流程（移植 HMCL 的 ForgeNewInstallTask），
    不依赖 installer 的 `--installClient`，所以老的 Forge / NeoForge 1.20.1 也能装
  - 实例名不变，启动它就是对应加载器
  - 原版 JSON 备份成 `<id>.json.vanilla-backup`，随时可以「卸载加载器」还原
  - 一个版本同时只能装一个加载器；装第二个会提示先卸载
  - NeoForge 的版本列表走 BMCLAPI 专用接口，installer 需要的库会先用镜像预下载
    （NeoForge installer 不认 `--mirror`，不预下载会卡在 maven.neoforged.net 超时）
- **导入整合包**：Modrinth `.mrpack`、MCBBS（`mcbbs.packmeta`）、CurseForge（`manifest.json`）、
  MultiMC/Prism（`mmc-pack.json`）、HMCL 导出包、带 `.minecraft` 结构的 zip；
  自动装原版 + 加载器，下载清单文件并解压 overrides
- CurseForge 文件默认走 `mod.mcimirror.top` 代理解析（不需要 API key）；
  想用官方 API 就设置环境变量 `GE_CURSE_API_KEY` 或配置 `curse_api_key`（不内置，避免泄露）
- **Java**：按版本要求的 `javaVersion` 自动挑（≥ 要求里最小的那个）；本机缺 Java 时弹窗询问，
  可直接从 Adoptium 下载安装 Temurin JDK 到 `<游戏目录>/launcher/java/`，并加入候选列表
- **账号**：离线账号（创建时校验只允许字母/数字/下划线）；微软账号见下方"账号"一节
- **界面**：CustomTkinter 实现，版本 / 账号 / 设置三个一级页面；支持浅色 / 深色 / 跟随系统主题
- **安装/导入可取消**：日志窗口带"取消"按钮，取消后杀掉 installer/processor 子进程并清理这次新建的残留
- **版本详情**：实例设置（内存、Java、窗口尺寸、日志窗口）、启动、浏览（日志/版本目录/世界/模组/资源包/光影包/截图）
- **日志**：每个游戏进程一个独立日志窗口，可自动滚动 / 清空 / 保存
- **验收**：`verify` 子命令做"进程存活 N 秒 + 日志关键字"的机械验收

## 快速开始

```bash
# 看一个游戏目录里有什么版本
python -m gelauncher versions --game-dir "D:\Minecraft\.minecraft"

# 打开图形界面（也可以用仓库根目录的 启动界面.bat 双击）
python -m gelauncher ui

# 只检查环境（Java / assets / libraries）
python -m gelauncher doctor
```

常用命令：

```bash
python -m gelauncher install --version-id 1.21.8 --game-dir <目录>   # 装原版（实例默认同名）
python -m gelauncher install --version-id 1.21.8 --instance-name 生存服 --game-dir <目录>   # 自定义实例名
python -m gelauncher install-loader --loader fabric --mc 1.21.8      # 装 Fabric
python -m gelauncher install-loader --loader forge  --mc 1.21.8      # 装 Forge
python -m gelauncher install-loader --loader neoforge --mc 26.3       # 装 NeoForge (26.3 = 26.3.0.x)
python -m gelauncher remove-loader  --loader fabric --mc 1.21.8      # 卸载还原
python -m gelauncher import-modpack --file 整合包.mrpack --instance-name 我的整合包  # 导入整合包
python -m gelauncher launch --version-id 1.21.8 --dry-run            # 只打印命令
python -m gelauncher launch --version-id 1.21.8                      # 启动
```

## 目录约定

- **版本隔离**：游戏工作目录默认是 `versions/<id>/`，所以 mods、saves、options.txt 都跟着版本走
  （和 HMCL 的默认行为一致，能直接接管已有实例）
- `.minecraft` 根目录只放 `libraries/`、`assets/`、`versions/`
- 启动器自己的配置在 `%APPDATA%\GeLauncher\config.json`

## 账号

- **离线账号**：给个名字就能用，UUID 按 `md5("OfflinePlayer:<name>")` 算，适合本机几个人玩。
  创建时只允许大小写英文字母、数字和下划线（Minecraft 玩家名规则），中文/空格/特殊字符会被拦下
- **微软账号：目前还不支持使用**。设备代码流登录的代码已经写好，但需要 Mojang 对启动器应用做 API 审批；
  在获批之前正版登录进游戏会被拒绝，所以暂时请用离线账号。流程和审批说明见 [msa-setup.md](msa-setup.md)

> 微软登录这类服务的 token 只保存在本机 `config.json`，不会上传到任何服务器，也不要把它发给别人。

## 已知限制

- Forge **≤1.12.1** 的 installer 没有 `--installClient`（实测），所以只能用 Ge 启动已装好的老实例，不能用 Ge 安装
- **微软账号尚未支持**：等待 Mojang 审批，获批前请使用离线账号
- CurseForge 的官方 API 在部分网络下会被 Cloudflare 403，默认走第三方代理 `mod.mcimirror.top`；
  代理的可用性不由本项目控制
- 没做联机、模组管理、皮肤、自动更新——这些不在范围内

## 开发

```bash
python -m pip install -r requirements.txt   # 运行时依赖（customtkinter）
python -m pip install pytest                # 测试依赖
python -m pytest
```

代码结构：

```
gelauncher/
├── core/                 # 无界面核心，不 import tkinter
│   ├── cancel.py         # 协作式取消（CancelToken）
│   ├── java_install.py   # 自动下载安装 Temurin JDK
│   ├── forge_processors.py # Forge/NeoForge processors 安装
│   ├── modpack.py        # 整合包识别与导入
│   ├── models.py         # 版本 JSON → dataclass
│   ├── layout.py         # .minecraft 目录约定
│   ├── rules.py          # rules[] 求值
│   ├── libraries.py      # Maven 坐标 / classpath / natives 识别
│   ├── inherit.py        # inheritsFrom 合并
│   ├── natives.py        # 解压 natives（zip-slip 防护）
│   ├── command.py        # 占位符替换 / 离线 UUID
│   ├── java.py           # 找 Java、按版本挑
│   ├── net.py            # 下载器：重试 / 多源 / sha1 / .part
│   ├── install_vanilla.py# 装原版
│   ├── install_loader.py # 装 Fabric / Forge / NeoForge（都装进现有版本）
│   ├── microsoft.py      # 微软登录（设备代码流 → Xbox → Minecraft）
│   ├── accounts.py       # 把配置里的当前账号变成启动用账号
│   ├── logfmt.py         # 把游戏 stdout 的 log4j XML 转成可读日志
│   └── launch.py         # 检查 + 拼命令 + 起进程
├── cli.py                # 所有能力的命令行入口
└── ui/                   # CustomTkinter 界面（只是 core 的薄壳）
    ├── app.py            # 主窗口 / 导航 / 任务与预载
    ├── pages.py          # 版本、账号、设置三个页面
    ├── theme.py          # 主题/配色（浅色/深色/跟随系统）
    ├── instance_list.py  # Canvas 自绘的版本/账号列表
    ├── widgets.py        # Card/Combo/label/button 等复用组件
    ├── confirm.py        # 危险操作确认框（红色删除按钮）
    ├── offline_dialog.py # 离线账号创建（命名校验）
    ├── install_dialog.py / loader_dialog.py / import_dialog.py
    ├── log_window.py     # 独立日志窗口（安装时可取消）
    └── msa_login.py      # 微软登录对话框
```

## 打包

```bash
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

产物是目录分发形式：`dist/GeLauncher/GeLauncher.exe`（同目录下的 `_internal/` 要一起带）。
CustomTkinter 的主题/字体资源会由 spec 里的 `collect_data_files("customtkinter")` 一并打进去。
exe 图标来自 `packaging/GeLauncher.ico`（由仓库根目录的 `icon瓜.png` 生成，含 16～256 多尺寸）；exe 带完整版本资源和 manifest。

代码签名（消除 SmartScreen 警告）需要自备受信任的证书，配置方式见 [packaging/signing.md](packaging/signing.md)。

## 进度

- [x] M0 骨架 + 真实 JSON fixture + pytest
- [x] M1 离线启动原版 / Fabric / Forge
- [x] M2 安装原版（manifest + 镜像 + sha1）
- [x] M3 Fabric（meta profile）
- [x] M4 Forge / NeoForge（官方 installer + 预置库 + 合并进现有版本）
- [x] M5 UI（CustomTkinter：版本 / 账号 / 设置 + 独立日志窗口）
- [x] M5.5 深色主题、安装可取消、Java 自动安装、实例预载
- [ ] M6 打包分发

## 免责声明

本项目是**非官方**工具，与 Mojang Studios、Microsoft 没有任何关联，也未获其背书。

- 不包含、不分发任何 Minecraft 游戏文件；所有游戏内容由用户自己的账号和本地 `.minecraft` 提供
- 使用前请自行购买 Minecraft: Java Edition；本工具不会授予、伪造或共享游戏所有权
- 代码里下载的版本元数据、库文件和资源均来自 Mojang 的公开接口，版权归各自所有者
- 请遵守 [Minecraft 使用条款](https://www.minecraft.net/en-us/eula) 与 Microsoft 服务条款

## 许可证

[MIT](LICENSE)
