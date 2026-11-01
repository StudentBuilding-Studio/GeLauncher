"""自动安装 Java：本机缺少某个版本需要的 Java 时，从 Adoptium 下 Temurin JDK。

和 HMCL 的"自动下载 Java"一样：只下当前平台（windows/x64）的 JDK，
解压到 ``<游戏目录>/launcher/java/temurin-<大版本>/``，再把 java.exe 加进候选列表。
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .java import JavaError, JavaRuntime, probe_java
from .net import DownloadJob, Fetcher

ProgressHook = "callable"


class JavaInstallError(RuntimeError):
    """下载/解压 Java 失败。"""


def adoptium_url(major: int) -> str:
    return (
        f"https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jdk/"
        "hotspot/normal/eclipse"
    )


def _safe_extract(archive: zipfile.ZipFile, target: Path, cancel=None) -> None:
    root = target.resolve()
    members = [info for info in archive.infolist() if not info.is_dir()]
    for index, info in enumerate(members, start=1):
        if cancel is not None and cancel.cancelled():
            from .cancel import Cancelled

            raise Cancelled("任务已取消")
        name = info.filename.replace("\\", "/")
        parts = [part for part in name.split("/") if part and part not in (".", "..")]
        if not parts:
            continue
        destination = (root / Path(*parts)).resolve()
        try:
            destination.relative_to(root)
        except ValueError:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("wb") as sink:
            shutil.copyfileobj(source, sink)
        if index % 200 == 0 and callable(cancel_progress := None):
            pass


def install_java(
    game_root: Path,
    major: int,
    *,
    fetcher: Fetcher,
    progress=None,
    cancel=None,
) -> JavaRuntime:
    """下载 Temurin JDK ``major`` 并返回探测到的运行时。"""
    from dataclasses import replace

    java_root = Path(game_root) / "launcher" / "java"
    java_root.mkdir(parents=True, exist_ok=True)
    archive_path = java_root / f"temurin-{major}.zip"
    target = java_root / f"temurin-{major}"

    if archive_path.is_file() and archive_path.stat().st_size < 1024 * 1024:
        archive_path.unlink(missing_ok=True)

    if not archive_path.is_file():
        if progress is not None:
            progress("java", f"下载 Temurin JDK {major}（约 190MB）…")
            local = replace(
                fetcher,
                progress=lambda written, total, label: progress(
                    "java", f"下载 Java：{written // 1048576}/{total // 1048576} MB"
                ),
            )
        else:
            local = fetcher
        report = local.download(
            DownloadJob(
                urls=[adoptium_url(major)],
                destination=archive_path,
                label=f"Temurin JDK {major}",
            )
        )
        if not report.ok:
            raise JavaInstallError(f"Java 下载失败：{report.failures[:2]}")

    if progress is not None:
        progress("java", "解压 Java…")
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        _safe_extract(archive, target, cancel=cancel)

    executables = sorted(target.rglob("bin/java.exe"))
    if not executables:
        raise JavaInstallError("解压后没找到 bin/java.exe")
    runtime = probe_java(executables[0])
    if runtime is None:
        raise JavaError(f"下载的 Java 跑不起来：{executables[0]}")
    archive_path.unlink(missing_ok=True)
    if progress is not None:
        progress("java", f"Java {runtime.major} 已就位：{runtime.path}")
    return runtime
