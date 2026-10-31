"""规则求值：``rules[]`` 里的 os / arch / features 条件。

Minecraft 的官方语义（launcher spec）：
- 没有 rules → 允许；
- 有 rules → 从"不允许"开始，按顺序看每一条规则，命中的规则把结果改成它的 action；
- 最后命中谁就是谁。
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from typing import Mapping

from .models import Rule

#: 版本 JSON 里的 os.name → 我们内部用的名字
OS_WINDOWS = "windows"
OS_LINUX = "linux"
OS_OSX = "osx"

_SYSTEM_OS = {
    "Windows": OS_WINDOWS,
    "Linux": OS_LINUX,
    "Darwin": OS_OSX,
}

_DEFAULT_FEATURES: dict[str, bool] = {
    "is_demo_user": False,
    "has_custom_resolution": False,
    "has_quick_plays_support": False,
    "is_quick_play_singleplayer": False,
    "is_quick_play_multiplayer": False,
    "is_quick_play_realms": False,
}


def current_os() -> str:
    return _SYSTEM_OS.get(platform.system(), platform.system().lower())


def current_arch() -> str:
    """把机器的架构归一成 JSON 里的写法：x86 / x86_64 / arm64。"""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    return machine


@dataclass(frozen=True)
class Environment:
    """求值规则时需要的"当前环境"。默认取本机，测试里可以随便造。"""

    os_name: str = ""
    arch: str = ""
    os_version: str = ""
    features: Mapping[str, bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "os_name", self.os_name or current_os())
        object.__setattr__(self, "arch", self.arch or current_arch())
        object.__setattr__(self, "os_version", self.os_version or platform.version())
        if self.features is None:
            object.__setattr__(self, "features", dict(_DEFAULT_FEATURES))
        else:
            merged = dict(_DEFAULT_FEATURES)
            merged.update(self.features)
            object.__setattr__(self, "features", merged)


def _rule_matches(rule: Rule, env: Environment) -> bool:
    # 官方 JSON 里偶尔出现 {"os": {"name": "universal"}}，含义是"任何系统"。
    if rule.os_name is not None and rule.os_name != "universal" and rule.os_name != env.os_name:
        return False
    if rule.os_arch is not None and rule.os_arch != env.arch:
        return False
    if rule.os_version is not None:
        try:
            if not re.search(rule.os_version, env.os_version):
                return False
        except re.error:
            return False
    for feature, expected in rule.features.items():
        if bool(env.features.get(feature, False)) != expected:
            return False
    return True


def allow(rules: tuple[Rule, ...] | list[Rule] | None, env: Environment | None = None) -> bool:
    """``rules`` 在当前环境下是否放行。"""
    if not rules:
        return True
    env = env or Environment()
    allowed = False
    for rule in rules:
        if _rule_matches(rule, env):
            allowed = rule.action == "allow"
    return allowed


def describe_mismatch(rules: tuple[Rule, ...] | list[Rule] | None, env: Environment) -> str:
    """给报错用：说明这些规则为什么不匹配当前环境。"""
    env = env or Environment()
    parts = []
    for rule in rules or ():
        bits = []
        if rule.os_name:
            bits.append(f"os={rule.os_name}")
        if rule.os_arch:
            bits.append(f"arch={rule.os_arch}")
        if rule.os_version:
            bits.append(f"os.version~{rule.os_version}")
        if rule.features:
            bits.append("features=" + ",".join(f"{k}={v}" for k, v in rule.features.items()))
        parts.append(f"{rule.action}({' '.join(bits)})")
    return f"当前环境 os={env.os_name} arch={env.arch}；规则: {', '.join(parts) or '无'}"
