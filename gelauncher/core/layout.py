"""``.minecraft`` 目录约定。所有路径只在这里算，别处不要手拼字符串。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import VersionJson, VersionJsonError
from .rules import Environment, current_arch, current_os


def default_game_dir() -> Path:
    """没有配置时用官方启动器的默认目录。"""
    override = os.environ.get("GE_GAME_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / ".minecraft"
    return Path.home() / ".minecraft"


@dataclass(frozen=True)
class GameLayout:
    """一个游戏根目录（也就是 ``.minecraft``）的路径约定。"""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser())

    # ------------------------------------------------------------ 基本目录

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def libraries_dir(self) -> Path:
        return self.root / "libraries"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def indexes_dir(self) -> Path:
        return self.assets_dir / "indexes"

    @property
    def objects_dir(self) -> Path:
        return self.assets_dir / "objects"

    @property
    def log_configs_dir(self) -> Path:
        return self.assets_dir / "log_configs"

    @property
    def launcher_profiles_path(self) -> Path:
        return self.root / "launcher_profiles.json"

    # ------------------------------------------------------------ 版本目录

    def version_dir(self, version_id: str) -> Path:
        return self.versions_dir / version_id

    def version_json_path(self, version_id: str) -> Path:
        return self.version_dir(version_id) / f"{version_id}.json"

    def version_jar_path(self, jar_id: str) -> Path:
        return self.version_dir(jar_id) / f"{jar_id}.jar"

    def natives_dir(self, version_id: str, env: Environment | None = None) -> Path:
        env = env or Environment()
        return self.version_dir(version_id) / f"natives-{env.os_name}-{env.arch}"

    # ------------------------------------------------------------ 游戏目录

    def game_dir(self, version_id: str, isolated: bool = True) -> Path:
        """游戏工作目录。

        按用户要求：默认"一律隔离"，即 ``versions/<id>/``，
        这样 mods/saves/options.txt 都跟着版本走（和 HMCL 的默认行为一致）。
        """
        return self.version_dir(version_id) if isolated else self.root

    # ------------------------------------------------------------ 资源

    def asset_index_path(self, asset_index_id: str) -> Path:
        return self.indexes_dir / f"{asset_index_id}.json"

    def asset_object_path(self, digest: str) -> Path:
        return self.objects_dir / digest[:2] / digest

    def log_config_path(self, file_id: str) -> Path:
        return self.log_configs_dir / file_id

    # ------------------------------------------------------------ 列表

    def installed_versions(self) -> list[str]:
        """列出版本目录里带 JSON 的版本 id，按名字排序。"""
        if not self.versions_dir.is_dir():
            return []
        result = []
        for entry in self.versions_dir.iterdir():
            if entry.is_dir() and (entry / f"{entry.name}.json").is_file():
                result.append(entry.name)
        return sorted(result)

    def load_json(self, version_id: str) -> VersionJson:
        return VersionJson.load(self.version_json_path(version_id))

    # ------------------------------------------------------------ 实例

    def instance_exists(self, instance: str) -> bool:
        return self.version_json_path(instance).is_file()

    def actual_version(self, instance: str, _depth: int = 0) -> str:
        """实例对应的真实游戏版本。

        依次尝试：
        1. ``clientVersion``（本启动器新建的实例都写这个）
        2. ``inheritsFrom`` 链
        3. jar 内嵌的 ``version.json``（HMCL 等启动器合并出的实例没有前两者）
        4. 加载器库版本反推（``net.minecraftforge:forge:1.16.5-36.x`` → 1.16.5）
        5. 实例名
        """
        if _depth > 8:
            return instance
        try:
            version = self.load_json(instance)
        except VersionJsonError:
            return instance
        if version.client_version:
            return version.client_version
        if version.inherits_from:
            return self.actual_version(version.inherits_from, _depth + 1)
        embedded = _jar_game_version(self.version_jar_path(instance))
        if embedded:
            return embedded
        guessed = _loader_game_version(version)
        if guessed:
            return guessed
        return version.id or instance


def current_env() -> Environment:
    return Environment(os_name=current_os(), arch=current_arch())


_JAR_VERSION_CACHE: dict[tuple[str, int], str | None] = {}


def _jar_game_version(jar_path: Path) -> str | None:
    """从客户端 jar 里的 version.json 读真实版本号（带 mtime 缓存）。"""
    try:
        stat = jar_path.stat()
    except OSError:
        return None
    key = (str(jar_path).lower(), stat.st_mtime_ns)
    if key in _JAR_VERSION_CACHE:
        return _JAR_VERSION_CACHE[key]
    value: str | None = None
    try:
        import json
        import zipfile

        with zipfile.ZipFile(jar_path) as archive:
            payload = json.loads(archive.read("version.json").decode("utf-8-sig"))
        candidate = str(payload.get("id") or payload.get("name") or "").strip()
        value = candidate or None
    except Exception:  # noqa: BLE001 - jar 坏了/没有 version.json 就当没有
        value = None
    _JAR_VERSION_CACHE[key] = value
    return value


def _loader_game_version(version) -> str | None:
    """从 loader 库版本反推游戏版本：forge 1.16.5-36.x / neoforge 21.1.x → 1.21.1。"""
    import re

    for library in getattr(version, "libraries", None) or []:
        name = getattr(library, "name", "") or ""
        if name.startswith("net.minecraftforge:forge:"):
            tail = name.split(":", 3)[-1]
            match = re.match(r"(\d+\.\d+(?:\.\d+)?)-", tail)
            if match:
                return match.group(1)
        if name.startswith("net.neoforged:neoforge:"):
            tail = name.split(":", 3)[-1]
            match = re.match(r"(\d+)\.(\d+)\.", tail)
            if match:
                return f"1.{match.group(1)}.{match.group(2)}"
    return None
