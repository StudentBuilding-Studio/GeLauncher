"""``inheritsFrom`` 合并。

Fabric / Forge / 各种"补丁版"都靠这个字段：子版本只写自己改动的部分，
其余字段从父版本继承。合并语义按官方 launcher spec：

- 标量字段：子版本有就用子版本的；
- ``libraries``：父在前、子在后，同坐标的子版本覆盖父版本；
- ``arguments.jvm`` / ``arguments.game``：父在前、子在后拼接；
- ``downloads``：按 key 合并，子覆盖父。
"""

from __future__ import annotations

from dataclasses import replace

from . import libraries
from .layout import GameLayout
from .models import AssetIndex, Library, LoggingClient, VersionJson

MAX_CHAIN_DEPTH = 10


class InheritError(ValueError):
    """继承链断了 / 成环了 / 父版本不存在。"""


def load_chain(layout: GameLayout, version_id: str) -> list[VersionJson]:
    """从子到父读出整条链（index 0 是 ``version_id`` 自己）。"""
    chain: list[VersionJson] = []
    seen: set[str] = set()
    current = version_id
    while current:
        if current in seen:
            raise InheritError(f"inheritsFrom 成环：{' -> '.join([c.id for c in chain])} -> {current}")
        if len(chain) >= MAX_CHAIN_DEPTH:
            raise InheritError(f"inheritsFrom 超过 {MAX_CHAIN_DEPTH} 层，疑似异常：{version_id}")
        seen.add(current)
        version = layout.load_json(current)
        chain.append(version)
        parent_id = version.inherits_from
        if parent_id and parent_id == current:
            raise InheritError(f"版本 {current} 的 inheritsFrom 指向自己")
        current = parent_id or ""
    return chain


def _merge_libraries(
    parent: tuple[Library, ...], child: tuple[Library, ...]
) -> tuple[Library, ...]:
    """父在前；**同一个 group:artifact:classifier** 的库，用子版本的。

    去重键里刻意不含版本号：否则 Fabric profile 里的 ``asm:9.10.1`` 会和原版的
    ``asm:9.6`` 同时留在 classpath 上，Fabric 启动时会直接报
    "duplicate ASM classes found on classpath"（实测踩过）。
    classifier 必须留在键里，否则 natives 库会被普通库挤掉。
    """
    merged: dict[tuple, Library] = {}
    for library in parent:
        merged[libraries.library_key(library)] = library
    for library in child:
        merged[libraries.library_key(library)] = library
    return tuple(merged.values())


def merge(child: VersionJson, parent: VersionJson | None) -> VersionJson:
    """把 ``child`` 合并到已解析的 ``parent`` 上。"""
    if parent is None:
        # 顶层版本：jar 缺省指向自己
        return replace(child, jar=child.jar or child.id)

    downloads = dict(parent.downloads)
    downloads.update(child.downloads)

    return replace(
        child,
        main_class=child.main_class or parent.main_class,
        jvm_arguments=parent.jvm_arguments + child.jvm_arguments,
        game_arguments=parent.game_arguments + child.game_arguments,
        minecraft_arguments=child.minecraft_arguments or parent.minecraft_arguments,
        libraries=_merge_libraries(parent.libraries, child.libraries),
        asset_index=child.asset_index or parent.asset_index,
        assets=child.assets or parent.assets,
        downloads=downloads,
        logging=child.logging or parent.logging,
        java_version=child.java_version or parent.java_version,
        type=child.type or parent.type,
        # jar 决定用哪个版本的 client jar：子版本写了就用子的，否则沿用父版本的
        jar=child.jar or parent.jar or child.id,
        release_time=child.release_time or parent.release_time,
        inherits_from=None,
    )


def resolve(layout: GameLayout, version_id: str) -> VersionJson:
    """读出并合并整条继承链，返回"可以直接拿来启动"的版本对象。"""
    chain = load_chain(layout, version_id)
    # 从根版本开始往上叠，保证 libraries / arguments 都是"父在前、子在后"
    merged = merge(chain[-1], None)
    for version in reversed(chain[:-1]):
        merged = merge(version, merged)
    return merged
