"""占位符替换 + 拼 java 命令行。

所有外部命令都用 ``list[str]`` 传参，绝不拼一整个字符串再交给 shell
（路径里有空格/中文时会炸，见方案 §7）。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .models import ConditionalArg, VersionJson
from .rules import Environment, allow


class CommandError(ValueError):
    """拼命令行时发现缺占位符 / 参数有问题。"""


PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass(frozen=True)
class Account:
    """启动用的账号。

    离线账号：只给名字，UUID 由 :func:`offline_uuid` 算出来；
    微软账号：UUID / access_token / xuid 都来自登录结果，``user_type="msa"``。
    """

    name: str = "Player"
    uuid: str | None = None
    user_type: str = "legacy"
    access_token: str = "0"
    client_id: str = ""
    xuid: str = ""

    @property
    def resolved_uuid(self) -> str:
        return self.uuid or offline_uuid(self.name)

    @property
    def is_microsoft(self) -> bool:
        return self.user_type == "msa"

    @classmethod
    def offline(cls, name: str) -> "Account":
        return cls(name=name)

    @classmethod
    def microsoft(
        cls, name: str, uuid: str, access_token: str, xuid: str = "", client_id: str = ""
    ) -> "Account":
        return cls(
            name=name,
            uuid=uuid,
            user_type="msa",
            access_token=access_token,
            xuid=xuid,
            client_id=client_id,
        )


def offline_uuid(name: str) -> str:
    """离线 UUID：md5("OfflinePlayer:<name>") 打上 v3 标记，和 HMCL/官方离线一致。"""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    hexed = digest.hex()
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


@dataclass(frozen=True)
class Placeholders:
    """一次启动里所有 ``${...}`` 的值。"""

    values: Mapping[str, str] = field(default_factory=dict)
    os_name: str = ""
    arch: str = ""

    def substitute(self, text: str, unknown: list[str] | None = None) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in self.values:
                return self.values[key]
            if unknown is not None:
                unknown.append(key)
            return match.group(0)

        return PLACEHOLDER_RE.sub(replace, text)


def build_placeholders(
    *,
    version: VersionJson,
    version_name: str,
    account: Account,
    game_directory: str,
    assets_root: str,
    assets_index_name: str,
    natives_directory: str,
    library_directory: str,
    classpath: str,
    primary_jar_name: str,
    launcher_name: str,
    launcher_version: str,
    version_type: str = "",
    log_config_path: str | None = None,
    resolution: tuple[int, int] | None = None,
    quick_play_path: str | None = None,
) -> Placeholders:
    values: dict[str, str] = {
        "auth_player_name": account.name,
        "auth_uuid": account.resolved_uuid,
        "auth_access_token": account.access_token,
        "auth_session": f"token:{account.access_token}:{account.resolved_uuid}",
        "clientid": account.client_id,
        "auth_xuid": account.xuid,
        "user_type": account.user_type,
        "user_properties": "{}",
        "version_name": version_name,
        "version_type": version_type or version.type or "release",
        "game_directory": game_directory,
        "game_assets": assets_root,
        "assets_root": assets_root,
        "assets_index_name": assets_index_name,
        "natives_directory": natives_directory,
        "library_directory": library_directory,
        "classpath": classpath,
        "classpath_separator": os.pathsep,
        "primary_jar_name": primary_jar_name,
        "launcher_name": launcher_name,
        "launcher_version": launcher_version,
        "resolution_width": str(resolution[0]) if resolution else "854",
        "resolution_height": str(resolution[1]) if resolution else "480",
        "quickPlayPath": quick_play_path or "",
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": "",
        "quickPlayRealms": "",
    }
    if log_config_path is not None:
        values["path"] = log_config_path
    return Placeholders(values=values)


def expand_arguments(
    arguments: Iterable[str | ConditionalArg],
    placeholders: Placeholders,
    env: Environment,
) -> list[str]:
    """按 rules 过滤参数，再做占位符替换。"""
    result: list[str] = []
    unknown: list[str] = []
    for argument in arguments:
        if isinstance(argument, ConditionalArg):
            if not allow(argument.rules, env):
                continue
            for value in argument.value:
                result.append(placeholders.substitute(value, unknown))
        else:
            result.append(placeholders.substitute(argument, unknown))
    if unknown:
        keys = ", ".join(sorted(set(unknown)))
        raise CommandError(
            f"版本 JSON 里有未知占位符：{keys}。"
            "如果这是第三方版本（OptiFine 等），把这段日志发给开发者。"
        )
    return result


def legacy_jvm_arguments(placeholders: Placeholders) -> list[str]:
    """1.12 及更早版本没有 ``arguments.jvm``，用这份默认值。"""
    return [
        "-Djava.library.path=${natives_directory}",
        "-Dminecraft.launcher.brand=${launcher_name}",
        "-Dminecraft.launcher.version=${launcher_version}",
        "-cp",
        "${classpath}",
    ]


def legacy_game_arguments(minecraft_arguments: str) -> list[str]:
    """``minecraftArguments`` 是一整串，按空白切开即可（官方就是这么做的）。"""
    return [token for token in minecraft_arguments.split(" ") if token]
