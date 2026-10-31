# Ge 启动器

只给几个朋友用的极简 Minecraft 启动器：Python 3.11+ / Tkinter，无第三方运行时依赖。
方案见 [简化启动器方案.md](简化启动器方案.md)。

## 现在能做什么

```bash
# 看游戏目录里有啥
python -m gelauncher versions --game-dir D:\Minecraft\.minecraft

# 环境体检（Java / assets / libraries）
python -m gelauncher doctor --game-dir D:\Minecraft\.minecraft

# 装原版（库 / assets / 客户端 jar，带 sha1 校验和多源切换）
python -m gelauncher install --version-id 1.21.8 --game-dir <目录>
python -m gelauncher available --limit 20          # 看官方有哪些版本

# 装加载器（自动挑最新稳定版）
python -m gelauncher install-loader --loader fabric --mc 1.21.8 --game-dir <目录>
python -m gelauncher install-loader --loader forge  --mc 1.21.8 --game-dir <目录>

# 只看会拼出什么命令，不启动
python -m gelauncher launch --version-id 1.21.8 --dry-run

# 真的启动（离线账号，默认 Player）
python -m gelauncher launch --version-id 1.21.8

# 机械验收：启动后看进程是否存活 N 秒 + 日志关键字
python -m gelauncher verify --version-id 1.21.8 --seconds 25
```

游戏目录可以用 `--game-dir` 临时指定，或者：

```bash
python -m gelauncher config set game-dir D:\Minecraft\.minecraft
```

## 目录约定

- **版本隔离**：游戏工作目录默认是 `versions/<id>/`（mods、saves、options.txt 都跟着版本走），
  所以能直接接管 HMCL / PCL2 已经装好的实例。
- `.minecraft` 根目录只放 `libraries/`、`assets/`、`versions/`。

## 几个实测出来的坑（别再踩）

| 现象 | 原因 | 现在的做法 |
|---|---|---|
| Fabric 启动报 `duplicate ASM classes` | 合并 `inheritsFrom` 时按完整坐标去重，原版 asm 和 loader 的 asm 同时进 classpath | 按 `group:artifact:classifier` 去重，子版本覆盖父版本（[inherit.py](gelauncher/core/inherit.py)） |
| 下载慢到离谱 | 官方 maven 连不上时，每个文件都要白等一次 TLS 握手超时 | 5 秒可达性探测 + 按"源×主机"拉黑 + 记住最近成功的源（[net.py](gelauncher/core/net.py)） |
| Forge installer 卡在 `mcp_config@zip` | installer 跟随 BMCLAPI 302 到 cernet 镜像时被 403；同一个 URL 用 Python 下是 200 | 先按 `install_profile.json` 预下载它要的库，再让 installer 校验复用（[install_loader.py](gelauncher/core/install_loader.py)） |
| Fabric installer 下不下来 | `maven.fabricmc.net` 从本机 TLS 握手全程失败，BMCLAPI 又没有 fabric-installer 这个 jar | Fabric 不走 installer，改用 meta 的 `profile/json`（和 HMCL 一样），3.8 秒装完 |
| Fabric 首次启动写不出重映射缓存 | 本机沙箱把 `.fabric/remappedJars` 的写入挡了 | 只影响沙箱环境；正常用户目录没这个问题 |

## 已知限制

- Forge **≤1.12.1** 的 installer 没有 `--installClient`（实测），所以只能用 Ge 启动已装好的老实例，不能用 Ge 安装。
- 只有离线账号（不联机就不需要正版验证）。
- 还没做 UI 和打包。

## 开发

```bash
# 装 pytest（唯一的三方依赖，仅开发期用）
python -m pip install pytest

# 跑测试
python -m pytest
```

## 结构

```
gelauncher/
├── core/                # 无界面核心，不 import tkinter
│   ├── models.py        # 版本 JSON → dataclass
│   ├── layout.py        # .minecraft 目录约定
│   ├── rules.py         # rules[] 求值
│   ├── libraries.py     # Maven 坐标 / classpath / natives 识别
│   ├── inherit.py       # inheritsFrom 合并
│   ├── natives.py       # 解压 natives（zip-slip 防护）
│   ├── command.py       # 占位符替换 / 离线 UUID
│   ├── java.py          # 找 Java、按版本挑
│   ├── net.py           # 下载器：重试 / 多源 / sha1 / .part
│   ├── install_vanilla.py  # 装原版
│   ├── install_loader.py   # 装 Fabric / Forge
│   └── launch.py        # 检查 + 拼命令 + 起进程
├── cli.py               # 所有能力的命令行入口
└── ui/                  # Tkinter（还没写）
```

## 进度

- [x] M0 骨架 + 真实 JSON fixture + pytest
- [x] M1 离线启动原版 / Fabric / Forge
- [x] M2 安装原版（manifest + 镜像 + sha1，实装 1.21.8 / 526MB 通过）
- [x] M3 Fabric（meta profile，3.8 秒，启动通过）
- [x] M4 Forge（官方 installer + 预置库，启动通过）
- [ ] M5 Tkinter UI
- [ ] M6 打包分发
