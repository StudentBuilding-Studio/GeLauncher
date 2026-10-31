"""启动器自己的配置（放在 %APPDATA%\\GeLauncher\\config.json）。

刻意做得很小：只有"游戏目录、Java、玩家名、内存、窗口大小"。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / "GeLauncher"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    game_dir: str = ""
    java_path: str = ""
    username: str = "Player"
    max_memory_mb: int = 2048
    width: int | None = None
    height: int | None = None

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
                known = {item.name for item in fields(cls)}
                for key, value in raw.items():
                    if key in known and value is not None:
                        setattr(config, key, value)
        if not config.game_dir:
            from .core.layout import default_game_dir

            config.game_dir = str(default_game_dir())
        return config

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: value for key, value in asdict(self).items()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def game_dir_path(self) -> Path:
        from .core.layout import default_game_dir

        return Path(self.game_dir) if self.game_dir else default_game_dir()
