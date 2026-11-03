"""占位符替换、离线 UUID、参数按 features 过滤。"""

from __future__ import annotations

import hashlib
import os

import pytest

from gelauncher.core.command import (
    Account,
    CommandError,
    Placeholders,
    build_placeholders,
    expand_arguments,
    legacy_game_arguments,
    offline_uuid,
)
from gelauncher.core.models import ConditionalArg, Rule, VersionJson
from gelauncher.core.rules import Environment

from conftest import FIXTURES

ENV = Environment(os_name="windows", arch="x86_64", os_version="10.0.22631")


def _expected_uuid(name: str) -> str:
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    text = digest.hex()
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def test_offline_uuid_is_stable_and_v3():
    uid = offline_uuid("Player")
    assert uid == _expected_uuid("Player")
    assert uid == offline_uuid("Player")
    assert uid != offline_uuid("player")
    # 版本号在第三段的第一个字符上
    assert uid.split("-")[2].startswith("3")


def test_account_defaults():
    account = Account()
    assert account.name == "Player"
    assert account.resolved_uuid == offline_uuid("Player")
    assert account.user_type == "legacy"


def test_microsoft_account_uses_real_uuid_and_token():
    account = Account.microsoft(
        name="Steve", uuid="069a79f4-44e9-4726-a5be-fca90e38aaf5", access_token="MC-TOKEN"
    )
    assert account.is_microsoft
    assert account.resolved_uuid == "069a79f4-44e9-4726-a5be-fca90e38aaf5"
    assert account.user_type == "msa"

    version = VersionJson.load(FIXTURES / "vanilla-1.21.8.json")
    placeholders = build_placeholders(
        version=version,
        version_name="1.21.8",
        account=account,
        game_directory="g",
        assets_root="a",
        assets_index_name="26",
        natives_directory="n",
        library_directory="l",
        classpath="cp",
        primary_jar_name="1.21.8.jar",
        launcher_name="gelauncher",
        launcher_version="0.1.0",
    )
    assert placeholders.substitute("${auth_uuid}") == "069a79f4-44e9-4726-a5be-fca90e38aaf5"
    assert placeholders.substitute("${auth_access_token}") == "MC-TOKEN"
    assert placeholders.substitute("${user_type}") == "msa"


def test_placeholders_substitute():
    version = VersionJson.load(FIXTURES / "vanilla-1.21.8.json")
    placeholders = build_placeholders(
        version=version,
        version_name="1.21.8",
        account=Account("Steve"),
        game_directory=r"D:\games\1.21.8",
        assets_root=r"D:\games\assets",
        assets_index_name="26",
        natives_directory=r"D:\games\natives",
        library_directory=r"D:\games\libraries",
        classpath=os.pathsep.join(["a.jar", "b.jar"]),
        primary_jar_name="1.21.8.jar",
        launcher_name="gelauncher",
        launcher_version="0.1.0",
    )
    assert placeholders.substitute("--username ${auth_player_name}") == "--username Steve"
    assert placeholders.substitute("${assets_index_name}") == "26"
    assert placeholders.substitute("-cp ${classpath}") == "-cp " + os.pathsep.join(["a.jar", "b.jar"])
    assert placeholders.substitute("${launcher_name} ${launcher_version}") == "gelauncher 0.1.0"


def test_unknown_placeholder_is_reported():
    placeholders = Placeholders(values={"a": "1"})
    with pytest.raises(CommandError) as excinfo:
        expand_arguments(["${nope}"], placeholders, ENV)
    assert "nope" in str(excinfo.value)


def test_expand_filters_by_features():
    args = [
        "--username",
        "${auth_player_name}",
        ConditionalArg(value=("--demo",), rules=(Rule(action="allow", features={"is_demo_user": True}),)),
        ConditionalArg(
            value=("--width", "${resolution_width}"),
            rules=(Rule(action="allow", features={"has_custom_resolution": True}),),
        ),
    ]
    placeholders = Placeholders(values={"auth_player_name": "Steve", "resolution_width": "1280"})
    assert expand_arguments(args, placeholders, ENV) == ["--username", "Steve"]

    with_feature = Environment(
        os_name="windows", arch="x86_64", os_version="10.0", features={"has_custom_resolution": True}
    )
    assert expand_arguments(args, placeholders, with_feature) == [
        "--username",
        "Steve",
        "--width",
        "1280",
    ]


def test_legacy_arguments_split():
    tokens = legacy_game_arguments("--username ${auth_player_name} --gameDir ${game_directory}")
    assert tokens == ["--username", "${auth_player_name}", "--gameDir", "${game_directory}"]


def test_full_vanilla_expansion_has_no_unknown_placeholders():
    """真实 1.21.8 JSON 的所有占位符都必须能被替换。"""
    version = VersionJson.load(FIXTURES / "vanilla-1.21.8.json")
    placeholders = build_placeholders(
        version=version,
        version_name="1.21.8",
        account=Account(),
        game_directory="g",
        assets_root="assets",
        assets_index_name="26",
        natives_directory="n",
        library_directory="l",
        classpath="cp",
        primary_jar_name="1.21.8.jar",
        launcher_name="gelauncher",
        launcher_version="0.1.0",
        log_config_path="log4j2.xml",
    )
    result = expand_arguments(version.jvm_arguments, placeholders, ENV)
    assert "${" not in " ".join(result)
    game = expand_arguments(version.game_arguments, placeholders, ENV)
    assert "${" not in " ".join(game)
    assert "net.minecraft.client.main.Main" not in game

def test_redact_command_hides_access_token():
    from gelauncher.core.launch import redact_command

    text = redact_command("java --accessToken abc.def --gameDir x")
    assert "abc.def" not in text
    assert "--accessToken ***" in text

def test_rotating_sink_rotates(tmp_path):
    from gelauncher.core.launch import _RotatingSink

    sink = _RotatingSink(tmp_path / "game.log")
    sink.LIMIT = 10
    sink.write("x" * 20)
    sink.flush()
    assert (tmp_path / "game.log.1").is_file()
    sink.write("y")
    sink.close()

def test_pump_survives_formatter_failure(monkeypatch):
    import io

    from gelauncher.core import launch as launch_module

    class BadFormatter:
        def feed(self, line):
            raise RuntimeError("boom")

        def flush(self):
            return []

    monkeypatch.setattr(launch_module, "GameOutputFormatter", lambda: BadFormatter())
    sink = io.StringIO()
    seen = []
    launch_module._pump(io.StringIO("a\nb\n"), sink, seen.append)
    assert seen == ["a", "b"]
    assert "a" in sink.getvalue()


def test_pump_reads_normal_output():
    import io

    from gelauncher.core import launch as launch_module

    sink = io.StringIO()
    seen = []
    launch_module._pump(io.StringIO("[12:00:00] [main/INFO]: hello\n"), sink, seen.append)
    assert seen
    assert sink.getvalue()
