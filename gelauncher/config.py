"""启动器自己的配置（放在 %APPDATA%\\GeLauncher\\config.json）。

结构分三块：
- 全局：游戏目录、默认 Java、默认内存、默认是否开日志窗口；
- 账号：离线账号名列表 + 当前选中的那个；
- 实例：按版本 id 存"这个版本自己的设置"（HMCL 的"版本设置"）。

老版本的配置文件只有 username/max_memory_mb 等字段，load() 会补上缺省值，
不会因为字段变多而炸掉。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / "GeLauncher"


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULT_INSTANCE = {
    "max_memory_mb": None,   # None = 用全局默认
    "java_path": "",         # 空 = 自动查找
    "show_log_window": None,  # None = 用全局默认
    "width": None,
    "height": None,
}


def _norm(path: str) -> str:
    """路径比较用的规范形式：统一分隔符 + 忽略大小写。

    Windows 上 ``D:/x`` 和 ``D:\\x`` 是同一个目录，但字符串不相等；
    不做这一步，目录列表会出现同一个文件夹两次。
    """
    return os.path.normcase(os.path.normpath(str(path)))


def _clean_path(path: str) -> str:
    return str(Path(path))


@dataclass
class Config:
    # ---- 全局 ----
    game_dir: str = ""
    #: 已添加过的游戏目录（版本页左侧的"目录列表"）
    game_dirs: list[str] = field(default_factory=list)
    java_path: str = ""
    #: 已知的 java.exe 路径（下拉框的候选项，检测/手动添加都会往里放）
    java_paths: list[str] = field(default_factory=list)
    max_memory_mb: int = 2048
    width: int | None = None
    height: int | None = None
    show_log_window: bool = True
    #: 主窗口最后的位置/尺寸（"1040x680+100+50"）
    window_geometry: str = ""

    # ---- 账号 ----
    accounts: list[str] = field(default_factory=lambda: ["Player"])
    selected_account: str = "Player"

    # ---- 实例 ----
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 兼容老字段：CLI 的 --username 默认值用它
    username: str = "Player"

    # ---------------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        config = cls()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            if isinstance(raw, dict):
                config._apply(raw)
        if not config.game_dir:
            from .core.layout import default_game_dir

            config.game_dir = str(default_game_dir())
        config._normalize()
        return config

    def _apply(self, raw: dict) -> None:
        known = {item.name for item in fields(self)}
        for key, value in raw.items():
            if key in known and value is not None:
                setattr(self, key, value)
        # 老版本只有一个 username：当成账号列表的初始值
        legacy = raw.get("username")
        if isinstance(legacy, str) and legacy and not raw.get("accounts"):
            self.accounts = [legacy]
            self.selected_account = legacy

    def _normalize(self) -> None:
        self.accounts = [name for name in (self.accounts or []) if isinstance(name, str) and name.strip()]
        if not self.accounts:
            self.accounts = ["Player"]
        if self.selected_account not in self.accounts:
            self.selected_account = self.accounts[0]
        if self.username not in self.accounts:
            self.username = self.selected_account
        if not isinstance(self.instances, dict):
            self.instances = {}

        # 目录列表：统一分隔符、去重，并保证当前目录排在最前面
        dirs = [d for d in (self.game_dirs or []) if isinstance(d, str) and d.strip()]
        seen: set[str] = set()
        unique: list[str] = []
        if self.game_dir:
            cleaned = _clean_path(self.game_dir)
            unique.append(cleaned)
            seen.add(_norm(cleaned))
        for directory in dirs:
            cleaned = _clean_path(directory)
            key = _norm(cleaned)
            if key not in seen:
                seen.add(key)
                unique.append(cleaned)
        self.game_dirs = unique
        if unique:
            self.game_dir = unique[0]

        # Java 列表：去重
        methods: list[str] = []
        seen_java: set[str] = set()
        for path in self.java_paths or []:
            if not isinstance(path, str) or not path.strip():
                continue
            cleaned = _clean_path(path)
            key = _norm(cleaned)
            if key in seen_java:
                continue
            seen_java.add(key)
            methods.append(cleaned)
        self.java_paths = methods

    # ---------------------------------------------------------------- 目录 / Java

    def add_game_dir(self, path: str) -> bool:
        cleaned = _clean_path(path)
        key = _norm(cleaned)
        exists = {_norm(d) for d in self.game_dirs}
        self.game_dir = cleaned
        if key in exists:
            return False
        self.game_dirs.insert(0, cleaned)
        return True

    def forget_game_dir(self, path: str) -> None:
        key = _norm(path)
        self.game_dirs = [d for d in self.game_dirs if _norm(d) != key]
        if _norm(self.game_dir) == key:
            self.game_dir = self.game_dirs[0] if self.game_dirs else ""

    def add_java_path(self, path: str) -> bool:
        cleaned = _clean_path(path)
        if not cleaned.strip():
            return False
        if _norm(cleaned) in {_norm(p) for p in self.java_paths}:
            return False
        self.java_paths.append(cleaned)
        return True

    def java_choices(self) -> list[str]:
        """下拉框的候选：已知路径 + 当前默认（如果不在列表里）。"""
        choices = list(self.java_paths)
        if self.java_path and _norm(self.java_path) not in {_norm(p) for p in choices}:
            choices.insert(0, self.java_path)
        return choices

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: value for key, value in asdict(self).items()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def game_dir_path(self) -> Path:
        from .core.layout import default_game_dir

        return Path(self.game_dir) if self.game_dir else default_game_dir()

    # ---------------------------------------------------------------- 账号

    def add_account(self, name: str) -> bool:
        name = name.strip()
        if not name or name in self.accounts:
            return False
        self.accounts.append(name)
        self.select_account(name)
        return True

    def remove_account(self, name: str) -> bool:
        if name not in self.accounts:
            return False
        self.accounts.remove(name)
        if not self.accounts:
            self.accounts = ["Player"]
        if self.selected_account == name:
            self.selected_account = self.accounts[0]
        self.username = self.selected_account
        return True

    def select_account(self, name: str) -> bool:
        if name not in self.accounts:
            return False
        self.selected_account = name
        self.username = name
        return True

    # ---------------------------------------------------------------- 实例

    def instance(self, version_id: str) -> dict[str, Any]:
        """返回该版本"生效的"设置（实例值优先，缺的用全局默认）。"""
        stored = self.instances.get(version_id) or {}
        merged = dict(DEFAULT_INSTANCE)
        merged.update(stored)
        if merged["max_memory_mb"] in (None, ""):
            merged["max_memory_mb"] = self.max_memory_mb
        if merged["show_log_window"] is None:
            merged["show_log_window"] = self.show_log_window
        if not merged["java_path"]:
            merged["java_path"] = self.java_path
        return merged

    def update_instance(self, version_id: str, **values: Any) -> None:
        current = dict(self.instances.get(version_id) or {})
        current.update(values)
        self.instances[version_id] = current

    def forget_instance(self, version_id: str) -> None:
        self.instances.pop(version_id, None)
