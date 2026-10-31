"""版本 JSON 解析：用真实的 1.21.8 / 1.12.2 / Fabric / Forge JSON 当 fixture。"""

from __future__ import annotations

import pytest

from gelauncher.core.models import ConditionalArg, VersionJson, VersionJsonError

from conftest import FIXTURES, fixture_json

VANILLA = FIXTURES / "vanilla-1.21.8.json"
LEGACY = FIXTURES / "vanilla-1.12.2.json"
FORGE = FIXTURES / "forge-1.20.1.json"
FABRIC = FIXTURES / "fabric-1.21.6.json"


def test_vanilla_1_21_8():
    version = VersionJson.load(VANILLA)
    assert version.id == "1.21.8"
    assert version.main_class == "net.minecraft.client.main.Main"
    assert version.java_version == 21
    assert version.type == "release"
    assert version.asset_index is not None
    assert version.asset_index.id == "26"
    assert len(version.libraries) == 113
    assert version.downloads["client"].sha1 == "a19d9badbea944a4369fd0059e53bf7286597576"

    # jvm 参数里既有普通字符串，也有带 rules 的对象
    assert "-Djava.library.path=${natives_directory}" in version.jvm_arguments
    conditional = [arg for arg in version.jvm_arguments if isinstance(arg, ConditionalArg)]
    assert conditional, "1.21.8 的 jvm 参数里应该有带 rules 的条目"

    # 游戏参数里的自定义分辨率是 features 控制的
    game_conditional = [arg for arg in version.game_arguments if isinstance(arg, ConditionalArg)]
    assert any(arg.rules and "has_custom_resolution" in arg.rules[0].features for arg in game_conditional)


def test_legacy_1_12_2_uses_minecraft_arguments():
    version = VersionJson.load(LEGACY)
    assert version.id == "1.12.2"
    assert version.minecraft_arguments is not None
    assert "${auth_player_name}" in version.minecraft_arguments
    # 老格式没有 arguments 字段
    assert version.jvm_arguments == ()
    assert version.game_arguments == ()
    assert version.java_version == 8


def test_forge_json_has_placeholders():
    version = VersionJson.load(FORGE)
    assert version.main_class == "cpw.mods.bootstraplauncher.BootstrapLauncher"
    assert version.java_version == 17
    joined = " ".join(str(arg) for arg in version.jvm_arguments)
    assert "${library_directory}" in joined
    assert "${classpath_separator}" in joined


def test_fabric_json():
    version = VersionJson.load(FABRIC)
    assert version.main_class == "net.fabricmc.loader.impl.launch.knot.KnotClient"
    assert version.java_version == 21


def test_missing_required_field_reports_path():
    data = fixture_json("vanilla-1.21.8.json")
    del data["id"]
    with pytest.raises(VersionJsonError) as excinfo:
        VersionJson.from_dict(data, "version(test.json)")
    assert "id" in str(excinfo.value)
    assert "test.json" in str(excinfo.value)


def test_argument_accepts_string_and_list_values():
    data = {
        "id": "x",
        "arguments": {
            "game": [
                "--single",
                {"rules": [{"action": "allow", "os": {"name": "windows"}}], "value": ["--a", "--b"]},
            ]
        },
    }
    version = VersionJson.from_dict(data)
    assert version.game_arguments[0] == "--single"
    arg = version.game_arguments[1]
    assert isinstance(arg, ConditionalArg)
    assert arg.value == ("--a", "--b")
    assert arg.rules[0].os_name == "windows"


def test_bad_rules_action_is_rejected():
    with pytest.raises(VersionJsonError):
        VersionJson.from_dict(
            {"id": "x", "libraries": [{"name": "a:b:1", "rules": [{"action": "maybe"}]}]}
        )
