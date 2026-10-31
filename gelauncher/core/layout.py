"""``.minecraft`` 目录约定。所有路径只在这里算，别处不要手拼字符串。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import VersionJson
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


def current_env() -> Environment:
    return Environment(os_name=current_os(), arch=current_arch())
