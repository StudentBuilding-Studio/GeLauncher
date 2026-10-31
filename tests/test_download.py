"""下载器 + 原版安装：用本地 HTTP 服务器当"假官方"，不依赖外网。

覆盖：
- 正常安装（manifest → 版本 JSON → 客户端 jar → 库 → 资源索引 → 资源对象 → 日志配置）
- 第二次安装全部跳过（幂等）
- 主源 500 时自动切到备用源（按文件降级）
- sha1 不匹配时失败，而且不留 .part / 不覆盖正式文件
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gelauncher.core import install_vanilla
from gelauncher.core.layout import GameLayout
from gelauncher.core.net import DownloadJob, Fetcher, Source, verify

from conftest import FIXTURES


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class RepoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 安静点
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        server: "RepoServer" = self.server  # type: ignore[assignment]
        if path in server.fail_paths:
            self.send_response(500)
            self.end_headers()
            return
        data = server.routes.get(path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RepoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), RepoHandler)
        self.routes: dict[str, bytes] = {}
        self.fail_paths: set[str] = set()


def build_repo(server: RepoServer, port: int) -> dict:
    """搭一个最小的"官方仓库"，字段名全部沿用真实 1.21.8 JSON 的骨架。"""
    client_bytes = b"FAKE CLIENT JAR"
    lib_bytes = b"FAKE LIBRARY"
    asset_bytes = b"hello asset"
    log_bytes = b"<Configuration></Configuration>"

    objects = {"a.txt": {"hash": sha1(asset_bytes), "size": len(asset_bytes)}}
    index_bytes = json.dumps({"objects": objects}).encode()
    asset_sha1 = sha1(asset_bytes)

    skeleton = json.loads((FIXTURES / "vanilla-1.21.8.json").read_bytes())
    skeleton["id"] = "1.99.0"
    skeleton["jar"] = "1.99.0"
    skeleton["libraries"] = [
        {
            "name": "com.example:lib:1.0",
            "downloads": {
                "artifact": {
                    "path": "com/example/lib/1.0/lib-1.0.jar",
                    "url": "https://libraries.minecraft.net/com/example/lib/1.0/lib-1.0.jar",
                    "sha1": sha1(lib_bytes),
                    "size": len(lib_bytes),
                }
            },
        }
    ]
    skeleton["downloads"] = {
        "client": {
            "url": "https://piston-data.mojang.com/v1/objects/abc/client.jar",
            "sha1": sha1(client_bytes),
            "size": len(client_bytes),
        }
    }
    skeleton["assetIndex"] = {
        "id": "test",
        "url": "https://piston-meta.mojang.com/assets/indexes/test.json",
        "sha1": sha1(index_bytes),
        "size": len(index_bytes),
    }
    skeleton["assets"] = "test"
    skeleton["logging"] = {
        "client": {
            "file": {
                "id": "client-test.xml",
                "url": "https://piston-data.mojang.com/v1/objects/def/client-test.xml",
                "sha1": sha1(log_bytes),
                "size": len(log_bytes),
            },
            "argument": "-Dlog4j.configurationFile=${path}",
            "type": "log4j2-xml",
        }
    }
    version_bytes = json.dumps(skeleton).encode()
    manifest = {
        "latest": {"release": "1.99.0", "snapshot": "1.99.0"},
        "versions": [
            {
                "id": "1.99.0",
                "type": "release",
                "url": "https://piston-meta.mojang.com/versions/1.99.0/1.99.0.json",
                "sha1": sha1(version_bytes),
                "releaseTime": "2026-01-01T00:00:00+00:00",
            }
        ],
    }

    routes = {
        "/mc/game/version_manifest_v2.json": json.dumps(manifest).encode(),
        "/versions/1.99.0/1.99.0.json": version_bytes,
        "/v1/objects/abc/client.jar": client_bytes,
        "/maven/com/example/lib/1.0/lib-1.0.jar": lib_bytes,
        "/assets/indexes/test.json": index_bytes,
        f"/assets/{asset_sha1[:2]}/{asset_sha1}": asset_bytes,
        "/v1/objects/def/client-test.xml": log_bytes,
    }
    for prefix in ("/primary", "/mirror"):
        for path, data in routes.items():
            server.routes[prefix + path] = data
    return {"asset_sha1": asset_sha1, "downloaded_files": len(routes) - 1}


def make_sources(port: int) -> tuple[Source, Source]:
    """两个源：一个伪装官方，一个伪装镜像，都指向同一台本地服务器。"""

    def mapping(prefix: str):
        base = f"http://127.0.0.1:{port}{prefix}"
        return (
            ("piston-meta.mojang.com", base),
            ("piston-data.mojang.com", base),
            ("libraries.minecraft.net", f"{base}/maven"),
            ("resources.download.minecraft.net", f"{base}/assets"),
        )

    return (
        Source(name="primary", host_map=mapping("/primary")),
        Source(name="mirror", host_map=mapping("/mirror")),
    )


@pytest.fixture
def repo():
    server = RepoServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        meta = build_repo(server, server.server_address[1])
        yield server, meta
    finally:
        server.shutdown()
        server.server_close()


def test_install_version_downloads_everything(repo, tmp_path: Path):
    server, meta = repo
    layout = GameLayout(tmp_path / ".minecraft")
    primary, mirror = make_sources(server.server_address[1])
    fetcher = Fetcher(sources=(primary, mirror), retries=1, timeout=5, threads=2)

    report = install_vanilla.install_version(layout, "1.99.0", fetcher=fetcher)

    assert report.ok, report.downloads.failures
    assert layout.version_json_path("1.99.0").is_file()
    assert layout.version_jar_path("1.99.0").is_file()
    assert (layout.libraries_dir / "com/example/lib/1.0/lib-1.0.jar").is_file()
    assert layout.asset_index_path("test").is_file()
    assert layout.asset_object_path(meta["asset_sha1"]).is_file()
    assert layout.log_config_path("client-test.xml").is_file()
    assert layout.launcher_profiles_path.is_file()
    assert report.downloads.downloaded == meta["downloaded_files"]
    assert not list(layout.root.rglob("*.part"))


def test_install_is_idempotent(repo, tmp_path: Path):
    server, meta = repo
    layout = GameLayout(tmp_path / ".minecraft")
    primary, mirror = make_sources(server.server_address[1])
    fetcher = Fetcher(sources=(primary, mirror), retries=1, timeout=5, threads=2)

    install_vanilla.install_version(layout, "1.99.0", fetcher=fetcher)
    second = install_vanilla.install_version(layout, "1.99.0", fetcher=fetcher)

    assert second.ok
    assert second.downloads.downloaded == 0
    assert second.downloads.skipped == meta["downloaded_files"]


def test_falls_back_to_mirror_per_file(repo, tmp_path: Path):
    server, _meta = repo
    server.fail_paths.add("/primary/maven/com/example/lib/1.0/lib-1.0.jar")
    layout = GameLayout(tmp_path / ".minecraft")
    primary, mirror = make_sources(server.server_address[1])
    fetcher = Fetcher(sources=(primary, mirror), retries=1, timeout=5, threads=2)

    report = install_vanilla.install_version(layout, "1.99.0", fetcher=fetcher)

    assert report.ok, report.downloads.failures
    assert (layout.libraries_dir / "com/example/lib/1.0/lib-1.0.jar").is_file()


def test_sha1_mismatch_fails_and_leaves_no_part(tmp_path: Path):
    server = RepoServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        server.routes["/primary/download/bad.bin"] = b"corrupted"
        source = Source(
            name="primary", host_map=(("example.invalid", f"http://127.0.0.1:{port}/primary"),)
        )
        fetcher = Fetcher(sources=(source,), retries=1, timeout=5)
        destination = tmp_path / "bad.bin"
        report = fetcher.download(
            DownloadJob(
                urls=["https://example.invalid/download/bad.bin"],
                destination=destination,
                sha1=sha1(b"expected"),
                label="bad.bin",
            )
        )
        assert not report.ok
        assert not destination.exists()
        assert not destination.with_name(destination.name + ".part").exists()
        assert not verify(destination, sha1(b"expected"))
    finally:
        server.shutdown()
        server.server_close()
