"""收集本机可用的 Java：系统安装 + 启动器自己管理的运行时。

不放 tkinter，UI 和后台线程都能调用。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .java import JavaRuntime, installed_javas, probe_java_cached


def is_under_jre(path: Path) -> bool:
    """JDK 8 自带 jre/bin/java.exe，和 bin/java.exe 是同一套，跳过。"""
    if path.parent.name.lower() != "bin":
        return False
    home = path.parent.parent
    if home.name.lower() != "jre":
        return False
    return (home.parent / "bin" / "java.exe").is_file()


def migrate_legacy_java(layout_root: Path, managed_dir: Path) -> None:
    """旧版本把 Java 装在 <游戏目录>/launcher/java，搬到启动器数据目录。"""
    legacy = Path(layout_root) / "launcher" / "java"
    if not legacy.is_dir():
        return
    try:
        managed_dir.mkdir(parents=True, exist_ok=True)
        for child in list(legacy.iterdir()):
            if not child.is_dir() or not child.name.startswith("temurin-"):
                continue
            target = managed_dir / child.name
            if target.exists():
                continue
            try:
                shutil.move(str(child), str(target))
            except OSError:
                pass  # Java 正在运行/被占用时下次再迁移
        try:
            legacy.rmdir()  # 空的才删得掉
        except OSError:
            pass
    except OSError:
        pass


def collect_java_runtimes(layout_root: Path, managed_dir: Path) -> list[JavaRuntime]:
    """返回去重后的 Java 列表，按大版本从高到低排。"""
    migrate_legacy_java(layout_root, managed_dir)

    candidates: list[JavaRuntime] = list(installed_javas())
    for root in (Path(managed_dir), Path(layout_root) / "launcher" / "java"):
        if not root.is_dir():
            continue
        for exe in sorted(root.glob("**/bin/java.exe")):
            if is_under_jre(exe):
                continue
            runtime = probe_java_cached(exe)
            if runtime is not None:
                candidates.append(runtime)

    def _canonical(path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    # 同一个文件只留一份
    unique: list[JavaRuntime] = []
    seen: set[str] = set()
    for runtime in candidates:
        key = _canonical(runtime.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(runtime)

    # 同一个版本可能同时有 javapath 别名和真正的 JDK，优先保留真正的 JDK
    def _penalty(runtime: JavaRuntime) -> int:
        text = str(runtime.path).lower()
        return 1 if "javapath" in text else 0

    unique.sort(key=lambda item: (-item.major, _penalty(item), str(item.path).lower()))
    best: dict[tuple[int, str], JavaRuntime] = {}
    for runtime in unique:
        key = (runtime.major, getattr(runtime, "version", ""))
        if key not in best:
            best[key] = runtime
    return sorted(best.values(), key=lambda item: (-item.major, str(item.path).lower()))
