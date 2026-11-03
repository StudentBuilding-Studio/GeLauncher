"""加载器安装：Fabric 走 profile JSON，Forge 走 installer。

这里用本地 HTTP 服务器模拟 BMCLAPI 的 fabric-meta / forge 版本列表，
把"版本 id 怎么拼""库下到哪""profile 怎么写盘"这些容易写错的点锁住。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gelauncher.core import install_loader
from gelauncher.core.layout import GameLayout
from gelauncher.core.net import Fetcher, Source

from conftest import FIXTURES


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        server: "Server" = self.server  # type: ignore[assignment]
        data = server.routes.get(self.path.split("?")[0])
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.routes: dict[str, bytes] = {}


@pytest.fixture
def server():
    srv = Server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _source(port: int) -> Source:
    base = f"http://127.0.0.1:{port}"
    return Source(
        name="bmclapi",
        host_map=(
            ("meta.fabricmc.net", f"{base}/fabric-meta"),
            ("maven.fabricmc.net", f"{base}/maven"),
            ("maven.minecraftforge.net", f"{base}/maven"),
            # promotions 走本地（不建路由 → 404），逼它落到 maven-metadata 兜底
            ("files.minecraftforge.net", f"{base}/forge-site"),
        ),
    )


def _game_dir(tmp_path: Path, mc_version: str = "1.21.8") -> GameLayout:
    layout = GameLayout(tmp_path / ".minecraft")
    version = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))
    version["id"] = mc_version
    version["jar"] = mc_version
    path = layout.version_json_path(mc_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(version), encoding="utf-8")
    return layout


def test_install_fabric_from_profile_json(server, tmp_path: Path):
    lib_bytes = b"FAKE FABRIC LOADER"
    profile = {
        "id": "fabric-loader-0.99.1-1.21.8",
        "inheritsFrom": "1.21.8",
        "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
        "type": "release",
        "arguments": {"jvm": ["-cp", "${classpath}"], "game": []},
        "libraries": [
            {
                "name": "net.fabricmc:fabric-loader:0.99.1",
                "url": "https://maven.fabricmc.net/",
            }
        ],
    }
    server.routes["/fabric-meta/v2/versions/loader/1.21.8"] = json.dumps(
        [{"loader": {"version": "0.99.1", "stable": True}}]
    ).encode()
    server.routes["/fabric-meta/v2/versions/loader/1.21.8/0.99.1/profile/json"] = json.dumps(
        profile
    ).encode()
    server.routes["/maven/net/fabricmc/fabric-loader/0.99.1/fabric-loader-0.99.1.jar"] = lib_bytes

    layout = _game_dir(tmp_path)
    fetcher = Fetcher(sources=(_source(server.server_address[1]),), retries=1, timeout=5)
    report = install_loader.install_fabric(layout, "1.21.8", fetcher=fetcher)

    assert report.version_id == "fabric-loader-0.99.1-1.21.8"
    written = json.loads(
        layout.version_json_path(report.version_id).read_text(encoding="utf-8")
    )
    assert written["inheritsFrom"] == "1.21.8"
    assert (
        layout.libraries_dir / "net/fabricmc/fabric-loader/0.99.1/fabric-loader-0.99.1.jar"
    ).read_bytes() == lib_bytes


def test_fabric_requires_vanilla(tmp_path: Path):
    layout = GameLayout(tmp_path / ".minecraft")
    fetcher = Fetcher(sources=(), retries=1, timeout=5)
    with pytest.raises(install_loader.LoaderInstallError) as excinfo:
        install_loader.install_fabric(layout, "1.21.8", fetcher=fetcher)
    assert "原版" in str(excinfo.value)


def test_forge_version_prefers_mirror_list(server, monkeypatch):
    server.routes["/forge/minecraft/1.21.8"] = json.dumps(
        [{"version": "58.0.0"}, {"version": "58.1.26"}, {"version": "58.1.9"}, {"version": "58.2.0-beta"}]
    ).encode()
    # 让"镜像列表"读到本地服务器
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(
        install_loader, "forge_versions_from_mirror", lambda fetcher, mc: ["58.0.0", "58.1.26", "58.1.9", "58.2.0-beta"]
    )
    fetcher = Fetcher(sources=(_source(server.server_address[1]),), retries=1, timeout=5)
    # beta 要被过滤掉
    assert install_loader.forge_version_for(fetcher, "1.21.8") == "58.1.26"


def test_forge_version_falls_back_to_maven_metadata(server, monkeypatch):
    # BMCLAPI 的版本列表接口是写死的 URL，测试里直接把它和 promotions 都打死
    monkeypatch.setattr(install_loader, "forge_versions_from_mirror", lambda fetcher, mc: [])
    monkeypatch.setattr(
        install_loader,
        "forge_promotions",
        lambda fetcher: (_ for _ in ()).throw(RuntimeError("官方 promotions 超时")),
    )
    server.routes["/maven/net/minecraftforge/forge/maven-metadata.xml"] = (
        b"<metadata><versioning><versions>"
        b"<version>1.21.8-58.0.0</version><version>1.21.8-58.1.3</version>"
        b"<version>1.20.1-47.4.0</version>"
        b"</versions></versioning></metadata>"
    )
    fetcher = Fetcher(sources=(_source(server.server_address[1]),), retries=1, timeout=5)
    assert install_loader.forge_version_for(fetcher, "1.21.8") == "58.1.3"


def test_version_id_helpers():
    assert install_loader.fabric_version_id("1.21.8", "0.16.14") == "fabric-loader-0.16.14-1.21.8"
    assert install_loader.forge_version_id("1.20.1", "1.20.1-47.4.0") == "1.20.1-forge-47.4.0"
    assert install_loader.forge_version_id("1.20.1", "47.4.0") == "1.20.1-forge-47.4.0"


def test_verify_installed_rejects_wrong_parent(tmp_path: Path):
    layout = _game_dir(tmp_path)
    bad = layout.version_json_path("1.21.8-forge-58.1.26")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({"id": "x", "inheritsFrom": "1.20.1"}), encoding="utf-8")
    with pytest.raises(install_loader.LoaderInstallError) as excinfo:
        install_loader.verify_installed(layout, "1.21.8-forge-58.1.26", "1.21.8")
    assert "inheritsFrom" in str(excinfo.value)


def test_merge_dedupes_same_coordinate_different_version():
    """Fabric profile 带的新版 asm 必须挤掉原版的老版，否则 Fabric 拒绝启动。"""
    from gelauncher.core.inherit import merge
    from gelauncher.core.models import Library, VersionJson

    parent = VersionJson(
        id="parent",
        libraries=(
            Library(name="org.ow2.asm:asm:9.6"),
            Library(name="org.lwjgl:lwjgl:3.3.3"),
        ),
    )
    child = VersionJson(
        id="child",
        inherits_from="parent",
        libraries=(
            Library(name="org.ow2.asm:asm:9.10.1"),
            Library(name="org.lwjgl:lwjgl:3.3.3:natives-windows"),
        ),
    )
    merged = merge(child, parent)
    names = [library.name for library in merged.libraries]
    assert "org.ow2.asm:asm:9.6" not in names
    assert "org.ow2.asm:asm:9.10.1" in names
    # classifier 不同的要留住
    assert "org.lwjgl:lwjgl:3.3.3:natives-windows" in names

def test_run_installer_aborts_when_silent(monkeypatch, tmp_path):
    """installer 长时间没有输出要快速失败，而不是干等 15 分钟。"""
    import io

    from gelauncher.core import install_loader
    from gelauncher.core.install_loader import LoaderInstallError, run_installer
    from gelauncher.core.java import JavaRuntime

    class FakeProcess:
        def __init__(self):
            self.stdout = io.StringIO("")
            self.killed = False
            self.returncode = None

        def poll(self):
            return -9 if self.killed else None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return -9

    monkeypatch.setattr(install_loader.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(install_loader, "INSTALLER_IDLE_TIMEOUT", 0.2)
    java = JavaRuntime(path=tmp_path / "java.exe", version="17")

    try:
        run_installer(java, tmp_path / "installer.jar", [], cwd=tmp_path)
    except LoaderInstallError as exc:
        assert "没有任何输出" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("installer 卡死时应当抛错")

def test_merge_loader_json_keeps_minecraft_arguments(tmp_path):
    import json

    from gelauncher.core.install_loader import _merge_loader_json
    from gelauncher.core.layout import GameLayout

    layout = GameLayout(tmp_path / "mc")
    version_dir = layout.root / "versions" / "1.12.2"
    version_dir.mkdir(parents=True)
    layout.version_json_path("1.12.2").write_text(
        json.dumps({"id": "1.12.2", "libraries": []}), encoding="utf-8"
    )
    patch = {
        "mainClass": "net.minecraft.launchwrapper.Launch",
        "minecraftArguments": "--tweakClass net.minecraftforge.fml.common.launcher.FMLTweaker",
        "libraries": [],
    }
    raw = _merge_loader_json(layout, "1.12.2", patch, loader="forge")

    assert raw["mainClass"] == "net.minecraft.launchwrapper.Launch"
    assert "--tweakClass" in raw["minecraftArguments"]
    assert raw["_geLoader"]["type"] == "forge"


def test_legacy_installer_root_jar_is_copied(tmp_path):
    import zipfile

    from gelauncher.core.forge_processors import _copy_legacy_root_jar
    from gelauncher.core.layout import GameLayout

    layout = GameLayout(tmp_path / "mc")
    installer = tmp_path / "forge-installer.jar"
    with zipfile.ZipFile(installer, "w") as archive:
        archive.writestr("forge-1.7.10-10.13.4.1614-universal.jar", b"fake universal")
    file_path = "net/minecraftforge/forge/1.7.10-10.13.4.1614/forge-1.7.10-10.13.4.1614-universal.jar"

    _copy_legacy_root_jar(installer, layout, {"filePath": file_path})

    copied = layout.libraries_dir / file_path
    assert copied.is_file()
    assert copied.read_bytes() == b"fake universal"


def test_missing_loader_library_fails_install(tmp_path, monkeypatch):
    """实例自带的库失败可以忍，加载器自己的库缺了必须报错。"""
    from gelauncher.core import install_loader
    from gelauncher.core.install_loader import LoaderInstallError, _attach_via_processors
    from gelauncher.core.layout import GameLayout
    from gelauncher.core.net import DownloadReport

    layout = GameLayout(tmp_path / "mc")
    version_dir = layout.root / "versions" / "1.12.2"
    version_dir.mkdir(parents=True)
    layout.version_json_path("1.12.2").write_text('{"id": "1.12.2", "libraries": []}', encoding="utf-8")

    patch = {
        "mainClass": "net.minecraft.launchwrapper.Launch",
        "minecraftArguments": "--tweakClass x",
        "libraries": [
            {"name": "net.minecraftforge:forge:1.12.2-14.23.5.2860"}
        ],
    }

    class FakeProfile:
        processors = []

    monkeypatch.setattr(install_loader, "read_profile", lambda path: FakeProfile(), raising=False)
    import gelauncher.core.forge_processors as fp

    monkeypatch.setattr(fp, "read_profile", lambda path: FakeProfile())
    monkeypatch.setattr(fp, "install_with_processors", lambda *a, **k: patch)

    class FakeFetcher:
        def download_all(self, jobs, should_stop=None):
            return DownloadReport()

    try:
        _attach_via_processors(
            layout,
            "1.12.2",
            "1.12.2",
            tmp_path / "installer.jar",
            loader="forge",
            fetcher=FakeFetcher(),
            java=None,
        )
    except LoaderInstallError as exc:
        assert "加载器库缺失" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("加载器库缺失时应当失败")
