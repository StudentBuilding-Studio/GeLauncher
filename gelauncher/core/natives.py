"""解压 natives。

两个必须做对的地方（方案 §7）：
1. zip-slip：压缩包里的条目名可能是 ``../../evil.dll``，解压前必须校验目标在目录内；
2. 重复解压：每个 natives jar 解出来的文件清单记在目录里的戳文件里，没变动就跳过。
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

NATIVES_STAMP = ".gelauncher-natives.json"
DEFAULT_EXCLUDES = ("META-INF/",)


class NativesError(RuntimeError):
    """解压 natives 失败。"""


def _safe_target(target_dir: Path, member_name: str) -> Path:
    """把压缩包里的条目名映射成本地路径，并挡住路径穿越。"""
    normalized = member_name.replace("\\", "/").lstrip("/")
    candidate = (target_dir / normalized).resolve()
    resolved_root = target_dir.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise NativesError(f"压缩包条目越界，拒绝解压：{member_name!r}")
    return candidate


def _is_excluded(name: str, excludes: Iterable[str]) -> bool:
    normalized = name.replace("\\", "/")
    return any(normalized.startswith(pattern) for pattern in excludes)


def _stamp(jars: Iterable[Path]) -> dict[str, int]:
    """用 (文件名, 大小, mtime) 判断压缩包有没有变过。"""
    result: dict[str, int] = {}
    for jar in jars:
        stat = jar.stat()
        result[jar.name] = int(stat.st_size) ^ int(stat.st_mtime_ns)
    return result


def extract_natives(
    jars: Iterable[Path],
    target_dir: Path,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    force: bool = False,
) -> list[Path]:
    """把若干 natives jar 里的文件解压到 ``target_dir``，返回解压出来的文件。"""
    jar_list = list(jars)
    target_dir = Path(target_dir)
    stamp_path = target_dir / NATIVES_STAMP
    excludes = tuple(excludes) or DEFAULT_EXCLUDES
    signature = _stamp(jar_list)

    if not force and stamp_path.is_file():
        try:
            if json.loads(stamp_path.read_text(encoding="utf-8")) == signature:
                return []
        except (json.JSONDecodeError, OSError):
            pass  # 戳文件坏了就当没做过

    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for jar in jar_list:
        try:
            archive = zipfile.ZipFile(jar)
        except zipfile.BadZipFile as exc:
            raise NativesError(f"natives 压缩包损坏：{jar}（{exc}）") from exc
        with archive:
            for info in archive.infolist():
                if info.is_dir() or _is_excluded(info.filename, excludes):
                    continue
                destination = _safe_target(target_dir, info.filename)
                if destination.is_dir():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                written.append(destination)

    stamp_path.write_text(json.dumps(signature), encoding="utf-8")
    return written
