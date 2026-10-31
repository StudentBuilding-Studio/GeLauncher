"""inheritsFrom 合并：用合成的小 JSON，行为更容易断言。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gelauncher.core import inherit
from gelauncher.core.inherit import InheritError
from gelauncher.core.layout import GameLayout

PARENT = {
    "id": "parent",
    "mainClass": "com.example.Parent",
    "type": "release",
    "javaVersion": {"majorVersion": 17},
    "assetIndex": {"id": "5", "url": "https://example/5.json"},
    "assets": "5",
    "downloads": {"client": {"path": "parent/parent.jar", "url": "https://example/parent.jar"}},
    "arguments": {
        "jvm": ["-cp", "${classpath}"],
        "game": ["--username", "${auth_player_name}"],
    },
    "libraries": [
        {"name": "a:a:1", "downloads": {"artifact": {"path": "a/a/1/a-1.jar"}}},
        {"name": "b:b:1", "downloads": {"artifact": {"path": "b/b/1/b-1.jar"}}},
    ],
}

CHILD = {
    "id": "child",
    "inheritsFrom": "parent",
    "mainClass": "com.example.Child",
    "libraries": [
        {"name": "a:a:1", "downloads": {"artifact": {"path": "a/a/1/a-1-patched.jar"}}},
        {"name": "c:c:1", "downloads": {"artifact": {"path": "c/c/1/c-1.jar"}}},
    ],
    "arguments": {"jvm": ["-Dchild=1"], "game": ["--child"]},
}


def _write(layout: GameLayout, version_id: str, payload: dict) -> None:
    path = layout.version_json_path(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def layout(tmp_path: Path) -> GameLayout:
    layout = GameLayout(tmp_path / ".minecraft")
    _write(layout, "parent", PARENT)
    _write(layout, "child", CHILD)
    return layout


def test_merge_overrides_scalars(layout: GameLayout):
    merged = inherit.resolve(layout, "child")
    assert merged.id == "child"
    assert merged.main_class == "com.example.Child"
    assert merged.java_version == 17
    assert merged.asset_index is not None and merged.asset_index.id == "5"
    assert merged.inherits_from is None


def test_merge_arguments_parent_first(layout: GameLayout):
    merged = inherit.resolve(layout, "child")
    assert [str(arg) for arg in merged.jvm_arguments] == ["-cp", "${classpath}", "-Dchild=1"]
    assert [str(arg) for arg in merged.game_arguments] == ["--username", "${auth_player_name}", "--child"]


def test_merge_libraries_child_wins(layout: GameLayout):
    merged = inherit.resolve(layout, "child")
    names = [lib.name for lib in merged.libraries]
    assert names == ["a:a:1", "b:b:1", "c:c:1"]
    patched = next(lib for lib in merged.libraries if lib.name == "a:a:1")
    assert patched.artifact is not None
    assert "patched" in patched.artifact.path


def test_jar_falls_back_to_parent(layout: GameLayout):
    merged = inherit.resolve(layout, "child")
    # 子版本没写 jar，就沿用父版本的 client jar
    assert merged.jar == "parent"

    payload = dict(CHILD)
    payload["jar"] = "child"
    _write(layout, "child", payload)
    assert inherit.resolve(layout, "child").jar == "child"


def test_downloads_merged(layout: GameLayout):
    merged = inherit.resolve(layout, "child")
    assert "client" in merged.downloads


def test_missing_parent_is_clear(layout: GameLayout):
    payload = dict(CHILD, inheritsFrom="does-not-exist")
    _write(layout, "child", payload)
    with pytest.raises(Exception) as excinfo:
        inherit.resolve(layout, "child")
    assert "does-not-exist" in str(excinfo.value)


def test_cycle_detected(layout: GameLayout):
    _write(layout, "parent", dict(PARENT, inheritsFrom="child"))
    with pytest.raises(InheritError):
        inherit.resolve(layout, "child")
