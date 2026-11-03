"""自动安装 Java：本机缺少某个版本需要的 Java 时，从 Adoptium 下 Temurin JDK。

和 HMCL 的"自动下载 Java"一样：只下当前平台（windows/x64）的 JDK，
解压到 ``<启动器数据目录>/java/temurin-<大版本>/``，再把 java.exe 加进候选列表。（和 HMCL 一样放在启动器自己的目录，不污染游戏目录。）
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
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


def adoptium_checksum(fetcher: Fetcher, major: int) -> str | None:
    """从 Adoptium API 取该版本 zip 的 sha256（取不到就算了，不阻止安装）。"""
    url = (
        f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
        "?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
    )
    try:
        raw = fetcher.get_bytes(url, label=f"Java {major} 校验值", ttl=600)
        data = json.loads(raw.decode("utf-8"))
        checksum = str(data[0]["binary"]["package"]["checksum"]).lower()
    except Exception:  # noqa: BLE001 - API 不通就不校验
        return None
    return checksum if len(checksum) == 64 else None


def _safe_extract(archive: zipfile.ZipFile, target: Path, cancel=None) -> None:
    root = target.resolve()
    members = [info for info in archive.infolist() if not info.is_dir()]
    total = sum(info.file_size for info in members)
    if len(members) > 200_000 or total > 8 * 1024 * 1024 * 1024:
        raise JavaInstallError("Java 压缩包异常（文件过多或解压后过大），已拒绝解压")
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
    java_root: Path,
    major: int,
    *,
    fetcher: Fetcher,
    progress=None,
    cancel=None,
) -> JavaRuntime:
    """下载 Temurin JDK ``major`` 到 ``java_root`` 并返回探测到的运行时。"""
    from dataclasses import replace

    java_root = Path(java_root)
    java_root.mkdir(parents=True, exist_ok=True)
    archive_path = java_root / f"temurin-{major}.zip"
    target = java_root / f"temurin-{major}"

    if archive_path.is_file() and archive_path.stat().st_size < 1024 * 1024:
        archive_path.unlink(missing_ok=True)

    if not archive_path.is_file():
        expected_sha256 = adoptium_checksum(fetcher, major)
        if progress is not None:
            progress("java", f"下载 Temurin JDK {major}（约 190MB）…")

            # 下载器每读一块都会回调；这里节流成每秒最多一次，避免日志被刷屏
            last_report = [0.0]
            best = [0]

            def _on_progress(written: int, total: int, _label: str) -> None:
                written = int(written or 0)
                total = int(total or 0)
                if written > best[0]:
                    best[0] = written
                now = time.monotonic()
                if now - last_report[0] < 1.0 and not (total and written >= total):
                    return
                last_report[0] = now
                done_mb = best[0] // 1048576
                if total:
                    progress(
                        "java",
                        f"下载 Java：{done_mb}/{total // 1048576} MB"
                        f"（{min(100, best[0] * 100 // total)}%）",
                    )
                else:
                    progress("java", f"下载 Java：{done_mb} MB")

            local = replace(fetcher, progress=_on_progress)
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
        if expected_sha256:
            actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if actual != expected_sha256:
                archive_path.unlink(missing_ok=True)
                raise JavaInstallError(
                    "Java 安装包 sha256 校验失败（可能下载被篡改/损坏），已删除，请重试。"
                )

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
