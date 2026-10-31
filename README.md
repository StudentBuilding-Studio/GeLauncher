# Ge 启动器

只给几个朋友用的极简 Minecraft 启动器：Python 3.11+ / Tkinter，无第三方运行时依赖。
方案见 [简化启动器方案.md](简化启动器方案.md)。

## 现在能做什么

```bash
# 看游戏目录里有啥
python -m gelauncher versions --game-dir D:\Minecraft\.minecraft

# 环境体检（Java / assets / libraries）
python -m gelauncher doctor --game-dir D:\Minecraft\.minecraft

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
├── core/            # 无界面核心，不 import tkinter
│   ├── models.py    # 版本 JSON → dataclass
│   ├── layout.py    # .minecraft 目录约定
│   ├── rules.py     # rules[] 求值
│   ├── libraries.py # Maven 坐标 / classpath / natives 识别
│   ├── inherit.py   # inheritsFrom 合并
│   ├── natives.py   # 解压 natives（zip-slip 防护）
│   ├── command.py   # 占位符替换 / 离线 UUID
│   ├── java.py      # 找 Java、按版本挑
│   └── launch.py    # 检查 + 拼命令 + 起进程
├── cli.py           # 所有能力的命令行入口
└── ui/              # Tkinter（还没写）
```

## 进度

- [x] M0 骨架 + 真实 JSON fixture + pytest
- [x] M1 离线启动原版（1.21.8 验收通过）
- [ ] M2 安装原版（version_manifest + 镜像 + sha1）
- [ ] M3 Fabric
- [ ] M4 Forge
- [ ] M5 Tkinter UI
- [ ] M6 打包分发
