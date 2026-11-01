"""把 Fabric 装进现有版本（不新建版本目录），以及卸载还原。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gelauncher.core import install_loader
from gelauncher.core.layout import GameLayout
from gelauncher.core.net import Fetcher

from conftest import FIXTURES

VANILLA = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))

FABRIC_PROFILE = {
    "id": "fabric-loader-0.20.1-1.21.8",
    "inheritsFrom": "1.21.8",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "type": "release",
    "arguments": {"jvm": [], "game": []},
    "libraries": [
        # 和原版同名但版本更高：应该顶掉原版的 asm
        {"name": "org.ow2.asm:asm:9.10.1", "url": "https://maven.fabricmc.net/"},
        {"name": "net.fabricmc:fabric-loader:0.20.1", "url": "https://maven.fabricmc.net/"},
        {"name": "net.fabricmc:intermediary:1.21.8", "url": "https://maven.fabricmc.net/"},
    ],
}


@pytest.fixture
def layout(tmp_path: Path) -> GameLayout:
    layout = GameLayout(tmp_path / ".minecraft")
    path = layout.version_json_path("1.21.8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(VANILLA, ensure_ascii=False, indent=2), encoding="utf-8")
    return layout


@pytest.fixture(autouse=True)
def stub_network(monkeypatch):
    """不联网：loader 版本、profile 固定，库下载直接跳过。"""
    monkeypatch.setattr(install_loader, "latest_fabric_loader", lambda fetcher, mc: "0.20.1")
    monkeypatch.setattr(install_loader, "fetch_fabric_profile", lambda fetcher, mc, ver: dict(FABRIC_PROFILE))
    monkeypatch.setattr(install_loader, "_library_jobs", lambda layout, version, env: [])
    # 让 attach 用空 fetcher
    monkeypatch.setattr(
        install_loader,
        "_ensure_cache",
        lambda fetcher, layout: fetcher,
    )


def _fetcher() -> Fetcher:
    return Fetcher(sources=(), retries=1, timeout=1)


def test_attach_fabric_merges_into_existing_version(layout: GameLayout):
    report = install_loader.attach_fabric(layout, "1.21.8", fetcher=_fetcher())
    assert report.version_id == "1.21.8"

    merged = json.loads(layout.version_json_path("1.21.8").read_text(encoding="utf-8"))
    assert merged["id"] == "1.21.8"
    assert merged["mainClass"] == "net.fabricmc.loader.impl.launch.knot.KnotClient"
    assert "inheritsFrom" not in merged

    names = [lib["name"] for lib in merged["libraries"]]
    assert "org.ow2.asm:asm:9.6" not in names, "原版老 asm 应该被顶掉"
    assert "org.ow2.asm:asm:9.10.1" in names
    assert "net.fabricmc:fabric-loader:0.20.1" in names
    # 原有的库不能丢
    assert len(merged["libraries"]) > len(FABRIC_PROFILE["libraries"])

    # 原版备份留了一份
    backup = layout.version_json_path("1.21.8").with_name("1.21.8.json.vanilla-backup")
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["mainClass"] == "net.minecraft.client.main.Main"
    assert install_loader.fabric_attached(layout, "1.21.8") is True


def test_attach_is_idempotent_and_backup_stays_vanilla(layout: GameLayout):
    install_loader.attach_fabric(layout, "1.21.8", fetcher=_fetcher())
    install_loader.attach_fabric(layout, "1.21.8", fetcher=_fetcher())

    backup = layout.version_json_path("1.21.8").with_name("1.21.8.json.vanilla-backup")
    assert json.loads(backup.read_text(encoding="utf-8"))["mainClass"] == "net.minecraft.client.main.Main"
    merged = json.loads(layout.version_json_path("1.21.8").read_text(encoding="utf-8"))
    names = [lib["name"] for lib in merged["libraries"]]
    assert names.count("net.fabricmc:fabric-loader:0.20.1") == 1


def test_detach_restores_vanilla(layout: GameLayout):
    install_loader.attach_fabric(layout, "1.21.8", fetcher=_fetcher())
    assert install_loader.detach_fabric(layout, "1.21.8") is True

    restored = json.loads(layout.version_json_path("1.21.8").read_text(encoding="utf-8"))
    assert restored["mainClass"] == "net.minecraft.client.main.Main"
    assert install_loader.fabric_attached(layout, "1.21.8") is False
    backup = layout.version_json_path("1.21.8").with_name("1.21.8.json.vanilla-backup")
    assert not backup.exists()
    # 再卸载一次：没有备份就直接返回 False
    assert install_loader.detach_fabric(layout, "1.21.8") is False


def test_fabric_library_entry_gets_downloads():
    entry = install_loader._fabric_library_entry(
        {"name": "net.fabricmc:fabric-loader:0.20.1", "url": "https://maven.fabricmc.net/"}
    )
    artifact = entry["downloads"]["artifact"]
    assert artifact["path"] == "net/fabricmc/fabric-loader/0.20.1/fabric-loader-0.20.1.jar"
    assert artifact["url"].endswith(artifact["path"])
    assert entry["url"] == "https://maven.fabricmc.net/", "原有字段要保留"


def test_maven_key_ignores_version_and_keeps_classifier():
    assert install_loader._maven_key("a.b:c:1.0") == install_loader._maven_key("a.b:c:2.0")
    assert install_loader._maven_key("a.b:c:1.0:natives-windows") != install_loader._maven_key("a.b:c:1.0")

# --------------------------------------------------------------------------- #
# Forge / NeoForge 也装进现有版本
# --------------------------------------------------------------------------- #


def test_neoforge_mc_version_mapping():
    from gelauncher.core.install_loader import _neoforge_mc_version

    assert _neoforge_mc_version("21.8.50") == "1.21.8"
    assert _neoforge_mc_version("21.1.209") == "1.21.1"
    assert _neoforge_mc_version("20.4.237") == "1.20.4"
    assert _neoforge_mc_version("26.3.0.100") == "26.3"
    assert _neoforge_mc_version("26.3.5.12") == "26.3.5"
    assert _neoforge_mc_version("garbage") is None


def test_neoforge_versions_from_bmcl_json(monkeypatch):
    from gelauncher.core import install_loader

    payload = json.dumps(
        [
            {"rawVersion": "21.8.50", "version": "21.8.50", "mcversion": "1.21.8"},
            {"rawVersion": "21.8.51-beta", "version": "21.8.51-beta", "mcversion": "1.21.8"},
        ]
    ).encode()

    class FakeFetcher:
        def get_bytes(self, url, label="", ttl=0.0):
            return payload

    versions = install_loader.neoforge_versions(FakeFetcher(), "1.21.8")
    assert versions == ["21.8.50"], "beta 不要，稳定版要留下"


def test_absorb_generated_forge_version(layout: GameLayout):
    """installer 生成的 Forge 版本合并进原版后，生成目录要删掉。"""
    from gelauncher.core import install_loader

    generated = "1.21.8-forge-58.1.26"
    patch = {
        "id": generated,
        "inheritsFrom": "1.21.8",
        "mainClass": "net.minecraftforge.bootstrap.ForgeBootstrap",
        "arguments": {"jvm": ["-Djava.net.preferIPv6Addresses=system"], "game": ["--launchTarget", "forge_client"]},
        "libraries": [
            {
                "name": "net.minecraftforge:forge:1.21.8-58.1.26:universal",
                "downloads": {
                    "artifact": {
                        "path": "net/minecraftforge/forge/1.21.8-58.1.26/forge-1.21.8-58.1.26-universal.jar",
                        "url": "https://maven.minecraftforge.net/x.jar",
                    }
                },
            },
            {"name": "org.ow2.asm:asm:9.10.1", "downloads": {"artifact": {"path": "p", "url": "u"}}},
        ],
    }
    path = layout.version_json_path(generated)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(patch), encoding="utf-8")

    install_loader._absorb_generated_version(layout, "1.21.8", generated, loader="forge")

    merged = json.loads(layout.version_json_path("1.21.8").read_text(encoding="utf-8"))
    assert merged["mainClass"] == "net.minecraftforge.bootstrap.ForgeBootstrap"
    assert "--launchTarget" in merged["arguments"]["game"]
    names = [lib["name"] for lib in merged["libraries"]]
    assert "net.minecraftforge:forge:1.21.8-58.1.26:universal" in names
    assert "org.ow2.asm:asm:9.6" not in names, "Forge 带的 asm 要顶掉原版老版本"
    assert not layout.version_dir(generated).exists(), "生成目录应该被删掉"
    assert install_loader.loader_of(layout, "1.21.8") == "forge"


def test_absorb_detects_neoforge(layout: GameLayout):
    from gelauncher.core import install_loader

    generated = "neoforge-26.3.0.100"
    patch = {
        "id": generated,
        "inheritsFrom": "1.21.8",
        "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
        "arguments": {"jvm": ["-Dfml.ignoreInvalidMinecraftCertificates=true"], "game": []},
        "libraries": [
            {"name": "net.neoforged:neoforge:26.3.0.100", "downloads": {"artifact": {"path": "p", "url": "u"}}}
        ],
    }
    path = layout.version_json_path(generated)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(patch), encoding="utf-8")

    install_loader._absorb_generated_version(layout, "1.21.8", generated, loader="neoforge")
    assert install_loader.loader_of(layout, "1.21.8") == "neoforge"


def test_refuses_second_loader(layout: GameLayout):
    from gelauncher.core import install_loader

    install_loader.attach_fabric(layout, "1.21.8", fetcher=_fetcher())
    with pytest.raises(install_loader.LoaderInstallError):
        install_loader._require_no_other_loader(layout, "1.21.8", "forge")
    # 同一个加载器重复装不算冲突
    install_loader._require_no_other_loader(layout, "1.21.8", "fabric")

def test_merge_keeps_argument_pairs(layout: GameLayout):
    """合并参数不能去重：--add-opens / --add-exports 是成对出现的。

    踩过的坑：原版 26.3 已经有 --add-exports，去重把 NeoForge 的那个 token 吞了，
    结果 jdk.naming.dns/... 被 JVM 当成主类，启动直接失败。
    """
    from gelauncher.core import install_loader

    patch = {
        "mainClass": "net.neoforged.fml.startup.Client",
        "arguments": {
            "jvm": ["--add-exports", "jdk.naming.dns/com.sun.jndi.dns=java.naming"],
            "game": [],
        },
        "libraries": [],
    }
    install_loader._merge_loader_json(layout, "1.21.8", patch, loader="neoforge")

    merged = json.loads(layout.version_json_path("1.21.8").read_text(encoding="utf-8"))
    jvm = merged["arguments"]["jvm"]
    pairs = list(zip(jvm, jvm[1:]))
    assert ("--add-exports", "jdk.naming.dns/com.sun.jndi.dns=java.naming") in pairs

# --------------------------------------------------------------------------- #
# 实例：实例名 = 文件夹名，版本号是参数
# --------------------------------------------------------------------------- #


def _make_instance(layout, name: str, version: str, main_class: str = "net.minecraft.client.main.Main"):
    data = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))
    data["id"] = name
    data["clientVersion"] = version
    path = layout.version_json_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    layout.version_jar_path(name).write_bytes(b"fake jar")
    return path


def test_actual_version_follows_client_version(tmp_path):
    from gelauncher.core.layout import GameLayout

    layout = GameLayout(tmp_path / ".minecraft")
    _make_instance(layout, "生存服", "1.21.8")
    assert layout.actual_version("生存服") == "1.21.8"

    # 别的启动器留下的实例：没有 clientVersion，只能顺着 inheritsFrom 找
    child = {
        "id": "1.21.8-fabric-demo",
        "inheritsFrom": "1.21.8",
        "mainClass": "x",
    }
    path = layout.version_json_path("1.21.8-fabric-demo")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(child), encoding="utf-8")
    assert layout.actual_version("1.21.8-fabric-demo") == "1.21.8"


def test_vexing_instance_names_are_rejected():
    from gelauncher.core.install_vanilla import InstallError, validate_instance_name

    for bad in ("", "  ", "a/b", "a\\b", "a:b", "..", "a.", "a?"):
        with pytest.raises(InstallError):
            validate_instance_name(bad)
    assert validate_instance_name(" 生存服 ") == "生存服"


def test_vanilla_install_writes_client_version(tmp_path):
    """确认安装出来的实例 JSON 里记了 clientVersion（版本号是参数，不是文件夹名）。"""
    from gelauncher.core.install_vanilla import install_version
    from gelauncher.core.layout import GameLayout
    from gelauncher.core.net import DownloadReport

    version_json = {
        "id": "1.99.0",
        "mainClass": "net.minecraft.client.main.Main",
        "libraries": [],
        "downloads": {"client": {"url": "https://example/client.jar", "size": 4}},
    }
    manifest = {"versions": [{"id": "1.99.0", "type": "release", "url": "https://example/1.99.0.json"}]}

    class FakeFetcher:
        cache_dir = "cache"

        def get_bytes(self, url, label="", ttl=0.0):
            return json.dumps(manifest).encode()

        def download(self, job):
            report = DownloadReport()
            destination = job.destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            if str(destination).endswith(".json"):
                destination.write_text(json.dumps(version_json), encoding="utf-8")
            else:
                destination.write_bytes(b"JAR!")
            report.downloaded = 1
            return report

        def download_all(self, jobs, on_done=None):
            return DownloadReport()

    layout = GameLayout(tmp_path / ".minecraft")
    install_version(layout, "1.99.0", instance_name="生存服", fetcher=FakeFetcher())

    data = json.loads(layout.version_json_path("生存服").read_text(encoding="utf-8"))
    assert data["id"] == "生存服"
    assert data["clientVersion"] == "1.99.0"
    assert layout.version_jar_path("生存服").is_file()
    assert layout.actual_version("生存服") == "1.99.0"

def test_processors_get_vanilla_shim(tmp_path, monkeypatch):
    """实例名 ≠ 版本号时，processors 之前必须先建好以版本号命名的原版目录。"""
    from gelauncher.core import install_loader
    from gelauncher.core.layout import GameLayout

    layout = GameLayout(tmp_path / ".minecraft")
    data = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))
    data["id"] = "生存服"
    data["clientVersion"] = "1.21.8"
    path = layout.version_json_path("生存服")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    layout.version_jar_path("生存服").write_bytes(b"jar")

    seen = {}

    def fake_attach(layout, instance, actual, installer, *, loader, fetcher, java, progress=None):
        seen["shim_jar"] = layout.version_jar_path(actual).is_file()
        seen["shim_json"] = layout.version_json_path(actual).is_file()
        install_loader._merge_loader_json(
            layout,
            instance,
            {"mainClass": "net.neoforged.fml.startup.Client", "libraries": [], "arguments": {}},
            loader="neoforge",
        )

    fetcher = install_loader.Fetcher(sources=(), retries=1, cache_dir=tmp_path)

    monkeypatch.setattr(install_loader, "_has_processors", lambda p: True)
    monkeypatch.setattr(install_loader, "_attach_via_processors", fake_attach)
    monkeypatch.setattr(
        install_loader,
        "_download_installer",
        lambda fetcher, urls, destination, label: destination.write_bytes(b"jar"),
    )
    monkeypatch.setattr(install_loader, "find_java", lambda *a, **k: type("J", (), {"major": 21, "path": "java"})())

    install_loader.install_neoforge(
        layout, "生存服", neoforge_version="21.8.57", fetcher=fetcher
    )
    assert seen["shim_jar"] and seen["shim_json"], "processors 运行时应该有 shim"
    assert not layout.version_dir("1.21.8").exists(), "装完要把临时 shim 删掉"
