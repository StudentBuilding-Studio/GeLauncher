"""Java 自动选择 + 自动安装 的核心逻辑测试。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from gelauncher.core import java, java_install
from gelauncher.core.java import JavaRuntime
from gelauncher.core.net import DownloadReport


def _runtime(path: str, version: str) -> JavaRuntime:
    return JavaRuntime(path=Path(path), version=version)


def test_choose_java_picks_smallest_satisfying(monkeypatch):
    table = {"C:/a/java.exe": _runtime("C:/a/java.exe", "21.0.1"), "C:/b/java.exe": _runtime("C:/b/java.exe", "25.0.1")}
    monkeypatch.setattr(java, "probe_java_cached", lambda p: table.get(str(p)))

    runtime, ok = java.choose_java(17, ["C:/a/java.exe", "C:/b/java.exe"])
    assert ok and runtime.major == 21

    runtime, ok = java.choose_java(26, ["C:/a/java.exe", "C:/b/java.exe"])
    assert not ok and runtime.major == 25

    # 全局默认满足要求时优先用它
    runtime, ok = java.choose_java(17, ["C:/a/java.exe", "C:/b/java.exe"], preferred="C:/b/java.exe")
    assert ok and runtime.major == 25

    runtime, ok = java.choose_java(17, [])
    assert runtime is None and not ok


def test_install_java_extracts_zip(tmp_path, monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("jdk-17.0.9+9/bin/java.exe", b"fake java")
        archive.writestr("jdk-17.0.9+9/bin/server/jvm.dll", b"dll")

    class FakeFetcher:
        def download(self, job):
            job.destination.parent.mkdir(parents=True, exist_ok=True)
            job.destination.write_bytes(payload.getvalue())
            report = DownloadReport()
            report.downloaded = 1
            return report

    monkeypatch.setattr(
        java_install, "probe_java", lambda path: _runtime(str(path), "17.0.9")
    )
    runtime = java_install.install_java(tmp_path / "java", 17, fetcher=FakeFetcher())
    assert runtime.major == 17
    expected = tmp_path / "java" / "temurin-17" / "jdk-17.0.9+9" / "bin" / "java.exe"
    assert expected.is_file()
    assert not (tmp_path / "java" / "temurin-17.zip").exists()

def test_java_major_compatible():
    assert java.java_major_compatible(8, 8)
    assert not java.java_major_compatible(8, 17)
    assert not java.java_major_compatible(8, 25)
    assert java.java_major_compatible(17, 17)
    assert java.java_major_compatible(17, 21)
    assert java.java_major_compatible(None, 25)

def test_install_java_checks_sha256(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import zipfile

    from gelauncher.core import java_install

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("jdk/bin/java.exe", b"fake")
    body = payload.getvalue()

    class FakeFetcher:
        def get_bytes(self, url, label="", ttl=0):
            return json.dumps(
                [{"binary": {"package": {"checksum": hashlib.sha256(b"other").hexdigest()}}}]
            ).encode()

        def download(self, job):
            job.destination.write_bytes(body)
            from gelauncher.core.net import DownloadReport

            report = DownloadReport()
            report.downloaded = 1
            return report

    monkeypatch.setattr(java_install, "probe_java", lambda path: None)
    try:
        java_install.install_java(tmp_path / "java", 17, fetcher=FakeFetcher())
    except java_install.JavaInstallError as exc:
        assert "sha256" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("校验失败时应当报错")
    assert not (tmp_path / "java" / "temurin-17.zip").exists()
