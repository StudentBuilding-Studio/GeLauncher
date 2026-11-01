"""processors 安装流程里可以离线测的部分：占位符、坐标解析、profile 读取。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gelauncher.core import forge_processors as fp


def test_replace_tokens_handles_braces_quotes_and_escapes():
    variables = {"A": "hello", "B": "world"}
    assert fp.replace_tokens(variables, "{A}-{B}") == "hello-world"
    assert fp.replace_tokens(variables, "'{A}'") == "{A}"
    assert fp.replace_tokens(variables, "a\\{b") == "a{b"


def test_replace_tokens_rejects_unknown_variable():
    with pytest.raises(fp.ProcessorError):
        fp.replace_tokens({}, "{NOPE}")


def test_artifact_relative_path():
    assert (
        fp.artifact_relative_path("net.minecraftforge:installertools:1.3.0")
        == "net/minecraftforge/installertools/1.3.0/installertools-1.3.0.jar"
    )
    assert (
        fp.artifact_relative_path("de.oceanlabs.mcp:mcp_config:1.20.1-20230612.114412:srg@zip")
        == "de/oceanlabs/mcp/mcp_config/1.20.1-20230612.114412/mcp_config-1.20.1-20230612.114412-srg.zip"
    )


def test_parse_literal_variants(tmp_path):
    from gelauncher.core.layout import GameLayout

    layout = GameLayout(tmp_path / ".minecraft")
    variables = {"SIDE": "client"}
    extract = lambda entry: tmp_path / Path(entry).name  # noqa: E731

    assert fp._parse_literal("{SIDE}", variables, layout, extract) == "client"
    assert fp._parse_literal("'literal'", variables, layout, extract) == "literal"
    assert fp._parse_literal(
        "[net.minecraftforge:installertools:1.3.0]", variables, layout, extract
    ) == str(fp.artifact_path(layout, "net.minecraftforge:installertools:1.3.0"))
    assert fp._parse_literal("--side={SIDE}", variables, layout, extract) == "--side=client"
    assert fp._parse_literal("/data/client.lzma", variables, layout, extract) == str(
        tmp_path / "client.lzma"
    )


def test_read_profile(tmp_path):
    installer = tmp_path / "fake-installer.jar"
    version = {"id": "1.20.1-neoforge-47.1.84", "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher"}
    profile = {
        "spec": 1,
        "minecraft": "1.20.1",
        "version": "47.1.84",
        "json": "/version.json",
        "data": {"BINPATCH": {"client": "/data/client.lzma"}},
        "processors": [{"jar": "net.minecraftforge:installertools:1.3.0", "args": [], "outputs": {}}],
        "libraries": [],
    }
    with zipfile.ZipFile(installer, "w") as archive:
        archive.writestr("install_profile.json", json.dumps(profile))
        archive.writestr("version.json", json.dumps(version))
        archive.writestr("data/client.lzma", b"binary")

    result = fp.read_profile(installer)
    assert result.minecraft == "1.20.1"
    assert result.version == "47.1.84"
    assert result.processors and result.data
    assert result.version_json["mainClass"].startswith("cpw.mods")
