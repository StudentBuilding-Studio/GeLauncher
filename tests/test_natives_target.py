"""natives 解压目录的选择规则（1.21 平铺 vs 26.x 子目录）。"""

from __future__ import annotations

from pathlib import Path

from gelauncher.core.launch import _natives_target_dir
from gelauncher.core.models import VersionJson

BASE = Path(r"D:\mc\versions\26.3\natives-windows-x86_64")


def test_old_layout_extracts_to_base():
    version = VersionJson.from_dict(
        {
            "id": "1.21.8",
            "arguments": {
                "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
                "game": [],
            },
        }
    )
    assert _natives_target_dir(version, BASE) == BASE


def test_new_layout_extracts_into_subdirectory():
    version = VersionJson.from_dict(
        {
            "id": "26.3",
            "arguments": {
                "jvm": [
                    "--enable-native-access=ALL-UNNAMED",
                    "-Djava.library.path=${natives_directory}/java",
                    "-Djna.tmpdir=${natives_directory}/jna",
                    "-Dorg.lwjgl.system.SharedLibraryExtractPath=${natives_directory}/lwjgl",
                ],
                "game": [],
            },
        }
    )
    assert _natives_target_dir(version, BASE) == BASE / "java"


def test_rejects_escaping_paths():
    version = VersionJson.from_dict(
        {
            "id": "x",
            "arguments": {"jvm": ["-Djava.library.path=${natives_directory}/../evil"], "game": []},
        }
    )
    assert _natives_target_dir(version, BASE) == BASE
