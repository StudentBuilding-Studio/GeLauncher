# Ge 启动器

简体中文 | [English](README.en.md)

一个只给几个朋友用的极简 Minecraft: Java Edition 启动器。Python 3.11+ / CustomTkinter（`pip install -r requirements.txt`），不需要编译。

支持原版 / Fabric / Forge / NeoForge，支持离线账号（微软账号等待 Mojang 审批，暂不可用），能直接接管 HMCL、PCL2、官方启动器已经装好的 `.minecraft`。

设计思路见 [简化启动器方案.md](简化启动器方案.md)。

## 功能

### 启动与实例

- 解析版本 JSON、合并 `inheritsFrom`、按规则过滤库、解压 natives、拼 `java` 命令
- **实例即文件夹**：`versions/<实例名>/`，游戏版本记在 JSON 的 `clientVersion` 里；实例名重复会阻止安装
- 默认版本隔离：mods、saves、options.txt 都跟着实例目录走
- 每个实例可单独设置：内存、Java、窗口尺寸、日志窗口、JVM 参数、进程优先级
- 实例设置支持「重置为默认」，回落到全局默认值

### 安装加载器

- 支持 Fabric / Forge / NeoForge，全部装进现有实例（和 HMCL 的 patch 一样）
- 点「装 Fabric / Forge / NeoForge」先选版本再安装
- Forge / NeoForge 优先走 `install_profile` / processors 流程，不依赖 installer 的 `--installClient`
  - 老版本 Forge（如 1.7.10 / 1.12.2）也能通过 `install_profile` 安装
  - 合并后的实例 JSON 会备份成 `<id>.json.vanilla-backup`，可随时「卸载加载器」还原
  - 一个实例同时只能装一个加载器
- installer 只有在 **sha1 校验通过** 时才会被执行；没有校验值的版本改走 profile 安装

### 导入整合包

- 支持 Modrinth `.mrpack`、CurseForge `manifest.json`、MCBBS、MultiMC/Prism、HMCL 导出包、带 `.minecraft` 结构的 zip
- 自动识别游戏版本与加载器，装原版 → 装加载器 → 下载清单文件 → 解压 overrides
- 整合包里的游戏版本默认只读展示；识别有误时可点「修改」手动更正
- CurseForge 走 `mod.mcimirror.top` 代理解析（不需要 API key）；也可用 `GE_CURSE_API_KEY` 或 `curse_api_key` 配官方 API

### Java

- 按版本要求的 `javaVersion` 自动挑选；老版本（要求 Java 8）不会误用 Java 17/25
- **每次启动前自动扫描本机 Java**，包括启动器自己安装的运行时
- 本机缺 Java 时会询问是否自动下载 Temurin JDK
- 自动安装的 Java 放在启动器数据目录 `%APPDATA%\GeLauncher\java\`，不污染游戏目录
- 支持系统代理（`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`，以及 Windows 系统代理）

### 界面

- CustomTkinter 界面：版本 / 账号 / 设置三个一级页面
- 浅色 / 深色 / 跟随系统主题
- 自绘标题栏：高清图标、可调字体、最小化 / 关闭，支持拖动
- 实例详情分「实例设置 / 自动安装 / 浏览 / 管理」，右侧内容可滚动
- 设置页与实例设置使用同一套行式布局，纯选项下拉只读

### 可靠性与安全

- 下载全程 sha1 校验、多源自动切换、`.part` 临时文件、单文件 4GB 上限
- installer 执行前必须通过 sha1 校验
- 下载/解压有大小与文件数上限，防 zip bomb 与磁盘写满
- 日志窗口里的 `--accessToken` 会打码
- `config.json` 损坏时先备份为 `.corrupt-时间戳`，不静默清空
- Windows 单实例锁，避免两个启动器同时改配置 / 装同一个实例
- 安装/导入可取消，取消后会清理这次新建的残留
- 游戏启动失败或异常退出（退出码非 0）会弹提示，可直接查看日志

## 快速开始

```bash
# 看一个游戏目录里有什么版本
python -m gelauncher versions --game-dir "D:\Minecraft\.minecraft"

# 打开图形界面（也可以双击仓库根目录的 启动界面.bat）
python -m gelauncher ui

# 只检查环境（Java / assets / libraries）
python -m gelauncher doctor
```

常用命令：

```bash
python -m gelauncher install --version-id 1.21.8 --game-dir <目录>   # 装原版（实例默认同名）
python -m gelauncher install --version-id 1.21.8 --instance-name 生存服 --game-dir <目录>
python -m gelauncher install-loader --loader fabric --mc 1.21.8
python -m gelauncher install-loader --loader forge  --mc 1.21.8
python -m gelauncher install-loader --loader neoforge --mc 26.3
python -m gelauncher remove-loader  --loader fabric --mc 1.21.8
python -m gelauncher import-modpack --file 整合包.mrpack --instance-name 我的整合包
python -m gelauncher launch --version-id 1.21.8 --dry-run
python -m gelauncher launch --version-id 1.21.8
```

## 目录约定

- **版本隔离**：游戏工作目录默认是 `versions/<id>/`
- `.minecraft` 根目录只放 `libraries/`、`assets/`、`versions/`
- 启动器配置：`%APPDATA%\GeLauncher\config.json`
- 自动安装的 Java：`%APPDATA%\GeLauncher\java\`
- 游戏日志：`<游戏目录>/logs/gelauncher-<实例>.log`（超过 20MB 轮转为 `.1`）

## 账号

- **离线账号**：只允许大小写字母、数字、下划线，长度不超过 16；UUID 按 `md5("OfflinePlayer:<name>")` 计算
- **微软账号：目前还不支持使用**。登录代码已经写好，但需要 Mojang 审批；获批前请用离线账号。
  流程见 [msa-setup.md](msa-setup.md)

> 账号 token 只保存在本机 `config.json`，不会上传到任何服务器。不要把 `config.json` 发给别人。

## 常见问题

**Q：游戏卡在 `Atlas Stitching ... shulker_boxes.png`？**

先等 2–3 分钟，首次启动要拼接所有 mod 的纹理图集，机械盘或杀软扫描时会很慢。
如果日志完全不再增长，再尝试移出可疑 mod（例如 Xaero 系列）、更新显卡驱动。

**Q：老版本（1.12.2 等）启动崩溃 `ClassCastException: AppClassLoader`？**

这是 Forge 用了 Java 9+。启动器会自动为要求 Java 8 的版本选择 Java 8；
也可以在实例设置里手动指定 Java 8。

**Q：Forge 安装提示"拿不到 installer 的 sha1 校验值"？**

出于安全考虑，未通过 sha1 校验的 installer 不会被执行。
如果该版本有 `install_profile`，启动器会自动改走 profile 安装；否则请检查网络能否访问 BMCLAPI。

**Q：下载卡住 / 公司网络需要代理？**

设置 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量，或使用 Windows 系统代理。`NO_PROXY` 可排除本地地址。

**Q：首次运行 Windows 提示"已保护你的电脑"？**

发布包没有代码签名，点「更多信息 → 仍要运行」即可。签名说明见 [packaging/signing.md](packaging/signing.md)。

## 已知限制

- **微软账号尚未支持**：等待 Mojang 审批
- 极老版本（1.7.10 等）的 Forge 安装依赖 installer 里的 `install_profile`；
  如果该 installer 连 profile 都没有，只能启动已经装好的实例
- CurseForge 官方 API 在部分网络下会被 Cloudflare 403，默认走第三方代理 `mod.mcimirror.top`，
  代理可用性不由本项目控制
- 没做联机、模组管理、皮肤、自动更新
- 自动安装的 Java 只支持 Windows x64

## 开发

```bash
python -m pip install -r requirements.txt   # 运行时依赖（customtkinter）
python -m pip install pytest                # 测试依赖
python -m pytest                            # 181 个测试
```

代码结构：

```
gelauncher/
├── core/                    # 无界面核心，不 import tkinter
│   ├── cancel.py            # 协作式取消（CancelToken）
│   ├── java.py              # 找 Java、按版本挑
│   ├── java_scan.py         # 扫描系统/启动器目录里的 Java、去重
│   ├── java_install.py      # 自动下载安装 Temurin JDK（sha256 校验）
│   ├── forge_processors.py  # Forge/NeoForge processors 安装、install_profile
│   ├── install_loader.py    # 装 Fabric / Forge / NeoForge
│   ├── install_vanilla.py   # 装原版
│   ├── modpack.py           # 整合包识别与导入
│   ├── models.py            # 版本 JSON → dataclass
│   ├── layout.py            # .minecraft 目录约定
│   ├── rules.py             # rules[] 求值
│   ├── libraries.py         # Maven 坐标 / classpath / natives
│   ├── inherit.py           # inheritsFrom 合并
│   ├── natives.py           # 解压 natives（zip-slip 防护）
│   ├── command.py           # 占位符替换 / 离线 UUID
│   ├── net.py               # 下载器：重试 / 多源 / sha1 / 代理 / 大小上限
│   ├── process_priority.py  # Windows 进程优先级
│   ├── repair.py            # 补全缺失文件
│   ├── microsoft.py         # 微软登录（设备代码流）
│   ├── accounts.py          # 当前账号 → 启动用账号
│   ├── logfmt.py            # log4j XML → 可读日志
│   └── launch.py            # 检查 + 拼命令 + 起进程 + 日志泵
├── cli.py                   # 命令行入口
└── ui/                      # CustomTkinter 界面
    ├── app.py               # 主窗口 / 导航 / 任务 / 单实例
    ├── pages.py             # 版本、账号、设置页面
    ├── titlebar.py          # 自绘标题栏
    ├── winframe.py          # 去掉系统标题栏 / 高分辨率图标
    ├── theme.py             # 主题与配色
    ├── instance_list.py     # Canvas 自绘列表
    ├── scrollbar.py         # 轻量滚动条
    ├── widgets.py           # Card/Combo/label/button/Dialog
    ├── confirm.py           # 危险操作确认框
    ├── single_instance.py   # 单实例锁
    ├── log_window.py        # 独立日志窗口
    ├── install_dialog.py / loader_dialog.py / import_dialog.py
    ├── offline_dialog.py / msa_login.py
    └── dpi.py               # DPI 感知
```

## 打包

```bash
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

产物是目录分发形式：`dist/GeLauncher/GeLauncher.exe`（同目录 `_internal/` 要一起带）。

- 图标：`packaging/GeLauncher.ico` 由 `icon瓜.png` 生成（16–256 多尺寸，最近邻缩放），
  改图后运行 `python packaging/make_icon.py`
- 发布 zip 与 `SHA256SUMS.txt` 会一起上传到 GitHub Release
- 代码签名说明见 [packaging/signing.md](packaging/signing.md)

## 进度

- [x] M0 骨架 + fixture + pytest
- [x] M1 离线启动原版 / Fabric / Forge
- [x] M2 安装原版（manifest + 镜像 + sha1）
- [x] M3 Fabric（meta profile）
- [x] M4 Forge / NeoForge（processors / install_profile）
- [x] M5 UI（CustomTkinter）
- [x] M5.5 主题、取消、Java 自动安装、预载
- [x] M6 打包分发
- [x] M7 单实例、系统代理、下载上限、日志轮转、安全校验

## 免责声明

本项目是**非官方**工具，与 Mojang Studios、Microsoft 没有任何关联，也未获其背书。

- 不包含、不分发任何 Minecraft 游戏文件
- 使用前请自行购买 Minecraft: Java Edition
- 下载的元数据、库和资源均来自公开接口，版权归各自所有者
- 请遵守 [Minecraft 使用条款](https://www.minecraft.net/en-us/eula) 与 Microsoft 服务条款

## 许可证

[MIT](LICENSE)
