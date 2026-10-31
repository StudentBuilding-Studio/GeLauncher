"""找 Java、读它的大版本、按 MC 版本挑选（方案 §7「Java 版本」）。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_VERSION_RE = re.compile(r'version "([^"]+)"')
_PROBE_TIMEOUT = 20


class JavaError(RuntimeError):
    """找不到 / 跑不起来 Java。"""


@dataclass(frozen=True)
class JavaRuntime:
    path: Path
    version: str
    vendor: str = ""

    @property
    def major(self) -> int:
        return major_of(self.version)

    def describe(self) -> str:
        return f"{self.path} (Java {self.version}{', ' + self.vendor if self.vendor else ''})"


def major_of(version: str) -> int:
    """``1.8.0_392`` → 8，``21.0.12`` → 21。"""
    parts = version.split(".")
    if parts and parts[0] == "1" and len(parts) > 1:
        return _to_int(parts[1])
    return _to_int(parts[0])


def _to_int(text: str) -> int:
    digits = re.match(r"\d+", text)
    return int(digits.group(0)) if digits else 0


def probe_java(path: Path) -> JavaRuntime | None:
    """跑一次 ``java -version``，解析出版本号。跑不起来就返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stderr or "") + (completed.stdout or "")
    match = _VERSION_RE.search(output)
    if not match:
        return None
    version = match.group(1)
    vendor = ""
    for line in output.splitlines():
        lowered = line.lower()
        if "runtime environment" in lowered or "vm (" in lowered:
            vendor = line.strip()
            break
    return JavaRuntime(path=path, version=version, vendor=vendor)


def _java_exe(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def _candidate_paths():
    """按"越可能是用户想要的越靠前"的顺序列出候选。"""
    seen: set[str] = set()

    def emit(path: Path | None):
        if path is None:
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        yield Path(path)

    home = os.environ.get("JAVA_HOME")
    if home:
        yield from emit(_java_exe(Path(home)))

    which = shutil.which("java")
    if which:
        yield from emit(Path(which))

    # 官方启动器下载的 runtime
    appdata = os.environ.get("APPDATA")
    if appdata:
        runtime_root = Path(appdata) / ".minecraft" / "runtime"
        if runtime_root.is_dir():
            for entry in runtime_root.glob("*/*/bin/java.exe"):
                yield from emit(entry)

    # 常见安装位置（只扫一层目录，别做全盘搜索）
    roots = [
        Path(r"C:\Program Files\Java"),
        Path(r"C:\Program Files\Eclipse Adoptium"),
        Path(r"C:\Program Files\Microsoft"),
        Path(r"C:\Program Files\Zulu"),
        Path(r"C:\Program Files\BellSoft"),
        Path(r"C:\Program Files\Amazon Corretto"),
        Path(r"C:\Program Files (x86)\Java"),
        Path(os.environ.get("LOCALAPPDATA", r"C:\Users")) / "Programs",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir():
                yield from emit(_java_exe(child))
                # jdk-21.0.12 下面直接是 bin
                yield from emit(child / "jre" / "bin" / "java.exe")


def installed_javas() -> list[JavaRuntime]:
    """扫一遍本机，返回能用的 Java（去重、按大版本降序）。"""
    result: dict[str, JavaRuntime] = {}
    for path in _candidate_paths():
        runtime = probe_java(path)
        if runtime is None:
            continue
        key = str(runtime.path).lower()
        result.setdefault(key, runtime)
    return sorted(result.values(), key=lambda item: item.major, reverse=True)


def find_java(
    required_major: int | None = None, configured: str | Path | None = None
) -> JavaRuntime:
    """挑一个 Java。

    - 指定了 ``configured``：只用它（跑不起来就报错，不静默换别的）；
    - 否则：选"大版本 >= 要求"里最小的那个；都不够就选最高的并让调用方警告。
    """
    if configured:
        runtime = probe_java(Path(configured))
        if runtime is None:
            raise JavaError(f"指定的 Java 跑不起来：{configured}")
        return runtime

    candidates = installed_javas()
    if not candidates:
        raise JavaError(
            "没有找到可用的 Java。请安装 Java 21（https://adoptium.net/），"
            "或用 --java 指定 java.exe 的路径。"
        )
    if required_major is None:
        return candidates[0]
    enough = [item for item in candidates if item.major >= required_major]
    if enough:
        return min(enough, key=lambda item: item.major)
    return candidates[0]
