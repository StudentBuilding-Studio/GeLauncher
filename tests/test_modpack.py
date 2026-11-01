"""整合包解析与导入（用合成 zip，不联网）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gelauncher.core import modpack
from gelauncher.core.layout import GameLayout
from gelauncher.core.net import DownloadReport


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_detect_modrinth(tmp_path):
    manifest = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.0.0",
        "name": "Fabulous Pack",
        "files": [
            {
                "path": "mods/sodium.jar",
                "downloads": ["https://cdn.example/sodium.jar"],
                "hashes": {"sha1": "abc"},
                "fileSize": 123,
                "env": {"client": "required"},
            },
            {
                "path": "mods/serveronly.jar",
                "downloads": ["https://cdn.example/server.jar"],
                "env": {"client": "unsupported"},
            },
        ],
        "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.15.11"},
    }
    archive = _zip(tmp_path / "pack.mrpack", {"modrinth.index.json": json.dumps(manifest).encode()})
    info = modpack.detect(archive)
    assert info.provider == "Modrinth"
    assert info.game_version == "1.20.1"
    assert (info.loader, info.loader_version) == ("fabric", "0.15.11")
    assert [entry.path for entry in info.files] == ["mods/sodium.jar"]
    assert info.files[0].sha1 == "abc"
    assert "overrides/" in info.overrides


def test_detect_hmcl_export(tmp_path):
    pack = {
        "jar": {"id": "1.20.1"},
        "patches": [{"id": "forge", "version": "47.1.84"}],
        "libraries": [],
    }
    entries = {
        "modpack.json": json.dumps({"name": "HMCL 包", "version": "2"}).encode(),
        "minecraft/pack.json": json.dumps(pack).encode(),
        "minecraft/mods/a.jar": b"mod",
    }
    info = modpack.detect(_zip(tmp_path / "hmcl.zip", entries))
    assert info.provider == "HMCL"
    assert info.game_version == "1.20.1"
    assert (info.loader, info.loader_version) == ("forge", "47.1.84")
    assert info.overrides == ["minecraft/"]


def test_detect_mcbbs(tmp_path):
    manifest = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "MCBBS 包",
        "version": "1.0",
        "addons": [
            {"id": "minecraft", "version": "1.12.2"},
            {"id": "forge", "version": "14.23.5.2859"},
        ],
        "files": [{"type": "addon", "path": "mods/x.jar", "hash": "deadbeef"}],
        "overrides": "overrides",
    }
    info = modpack.detect(_zip(tmp_path / "mcbbs.zip", {"mcbbs.packaging": json.dumps(manifest).encode()}))
    assert info.provider == "MCBBS"
    assert info.game_version == "1.12.2"
    assert (info.loader, info.loader_version) == ("forge", "14.23.5.2859")
    assert info.overrides == ["overrides/"]


def test_detect_manual_zip_with_version_json(tmp_path):
    version = {
        "id": "1.20.1",
        "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.15.11"}],
    }
    entries = {
        "mods/a.jar": b"mod",
        "config/x.toml": b"cfg",
        "versions/1.20.1/1.20.1.json": json.dumps(version).encode(),
    }
    info = modpack.detect(_zip(tmp_path / "manual.zip", entries))
    assert info.provider == "手动整合包"
    assert info.game_version == "1.20.1"
    assert (info.loader, info.loader_version) == ("fabric", "0.15.11")


def test_install_modpack_writes_files_and_metadata(tmp_path, monkeypatch):
    archive = tmp_path / "pack.mrpack"
    manifest = {
        "name": "测试包",
        "versionId": "1",
        "dependencies": {"minecraft": "1.20.1"},
        "files": [
            {
                "path": "mods/example.jar",
                "downloads": ["https://cdn.example/example.jar"],
                "hashes": {"sha1": "x"},
            }
        ],
    }
    _zip(
        archive,
        {
            "modrinth.index.json": json.dumps(manifest).encode(),
            "overrides/config/test.cfg": b"hello",
        },
    )
    layout = GameLayout(tmp_path / ".minecraft")

    def fake_install_version(layout, version_id, *, instance_name=None, fetcher=None):
        report = type("R", (), {})()
        report.ok = True
        report.downloads = DownloadReport()
        path = layout.version_json_path(instance_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": instance_name, "clientVersion": version_id}), encoding="utf-8")
        layout.version_jar_path(instance_name).write_bytes(b"jar")
        return report

    monkeypatch.setattr(modpack.install_vanilla, "install_version", fake_install_version)

    class FakeFetcher:
        def download_all(self, jobs, on_done=None):
            report = DownloadReport()
            for job in jobs:
                job.destination.parent.mkdir(parents=True, exist_ok=True)
                job.destination.write_bytes(b"downloaded")
                report.downloaded += 1
            return report

    info = modpack.detect(archive)
    report = modpack.install_modpack(
        layout, archive, instance_name="测试包", info=info, fetcher=FakeFetcher()
    )
    assert report.ok
    root = layout.version_dir("测试包")
    assert (root / "mods/example.jar").read_bytes() == b"downloaded"
    assert (root / "config/test.cfg").read_bytes() == b"hello"
    metadata = json.loads((root / "gelauncher-modpack.json").read_text(encoding="utf-8"))
    assert metadata["provider"] == "Modrinth"
    assert metadata["gameVersion"] == "1.20.1"


def test_install_modpack_rejects_existing_instance(tmp_path, monkeypatch):
    archive = _zip(
        tmp_path / "pack.mrpack",
        {"modrinth.index.json": json.dumps({"name": "x", "dependencies": {"minecraft": "1.20.1"}}).encode()},
    )
    layout = GameLayout(tmp_path / ".minecraft")
    path = layout.version_json_path("已有实例")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")

    info = modpack.detect(archive)
    with pytest.raises(modpack.install_vanilla.InstanceExistsError):
        modpack.install_modpack(
            layout,
            archive,
            instance_name="已有实例",
            info=info,
            fetcher=object(),
        )
