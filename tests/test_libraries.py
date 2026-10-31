"""库坐标、classpath 筛选、natives 识别。"""

from __future__ import annotations

import pytest

from gelauncher.core import libraries
from gelauncher.core.libraries import MavenCoordinate, LibraryError
from gelauncher.core.models import Artifact, Library, VersionJson
from gelauncher.core.rules import Environment

from conftest import FIXTURES

ENV = Environment(os_name="windows", arch="x86_64", os_version="10.0.22631")


@pytest.mark.parametrize(
    ("name", "path"),
    [
        (
            "com.mojang:jtracy:1.0.29:natives-windows",
            "com/mojang/jtracy/1.0.29/jtracy-1.0.29-natives-windows.jar",
        ),
        ("org.ow2.asm:asm:9.7.1", "org/ow2/asm/asm/9.7.1/asm-9.7.1.jar"),
        ("net.java.dev.jna:jna:5.17.0@jar", "net/java/dev/jna/jna/5.17.0/jna-5.17.0.jar"),
    ],
)
def test_maven_paths(name, path):
    assert MavenCoordinate.parse(name).path == path


def test_bad_coordinate():
    with pytest.raises(LibraryError):
        MavenCoordinate.parse("nonsense")
    with pytest.raises(LibraryError):
        MavenCoordinate.parse("a:b")


def test_modern_natives_are_not_on_classpath():
    version = VersionJson.load(FIXTURES / "vanilla-1.21.8.json")
    classpath, natives = libraries.effective_libraries(version, ENV)

    cp_paths = {libraries.library_relative_path(lib) for lib in classpath}
    assert "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3.jar" in cp_paths
    # natives 分类器不该出现在 classpath 里
    assert not any("-natives-windows" in path for path in cp_paths)
    # osx 专用库要被过滤掉
    assert not any("natives-macos" in path for path in cp_paths)

    native_paths = {libraries.library_relative_path(lib) for lib in natives}
    assert "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar" in native_paths
    assert len(natives) >= 6


def test_old_style_natives_classifier():
    version = VersionJson.load(FIXTURES / "vanilla-1.12.2.json")
    _classpath, natives = libraries.effective_libraries(version, ENV)
    assert natives, "1.12.2 应该有老格式的 natives 库"
    for library in natives:
        artifact, classifier = libraries.native_artifact(library, ENV)
        assert artifact is not None
        assert classifier and "natives-windows" in classifier


def test_osx_only_library_filtered_out():
    library = Library(
        name="ca.weblite:java-objc-bridge:1.1",
        artifact=Artifact(path="ca/weblite/java-objc-bridge/1.1/java-objc-bridge-1.1.jar"),
        rules=(),
    )
    assert libraries.is_native_library(library) is False


def test_natives_dir_name():
    assert libraries.natives_dir_name(ENV) == "natives-windows-x86_64"
    assert libraries.natives_dir_name(Environment(os_name="linux", arch="arm64", os_version="")) == (
        "natives-linux-arm64"
    )


def test_download_url_fallbacks():
    artifact = Artifact(path="a/a/1/a-1.jar", url=None)
    lib = Library(name="a:a:1", artifact=artifact, url="https://repo.example/")
    assert libraries.download_url(lib, artifact) == "https://repo.example/a/a/1/a-1.jar"
    with_url = Artifact(path="a/a/1/a-1.jar", url="https://cdn.example/a-1.jar")
    assert libraries.download_url(lib, with_url) == "https://cdn.example/a-1.jar"
