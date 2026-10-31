"""Maven 坐标、库路径、natives 库的识别。

这里只做"纯计算"，不碰磁盘；路径都返回相对 ``libraries/`` 的 POSIX 字符串，
由 :mod:`gelauncher.core.layout` 负责拼成真正的路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Artifact, Library, VersionJson
from .rules import Environment, allow


class LibraryError(ValueError):
    """库坐标或 classifier 有问题。"""


@dataclass(frozen=True)
class MavenCoordinate:
    group: str
    artifact: str
    version: str
    classifier: str | None = None
    extension: str = "jar"

    @classmethod
    def parse(cls, name: str) -> "MavenCoordinate":
        """解析 ``group:artifact:version[:classifier][@extension]``。"""
        if not name or ":" not in name:
            raise LibraryError(f"不是合法的 Maven 坐标：{name!r}")
        body, _, extension = name.partition("@")
        parts = body.split(":")
        if len(parts) == 3:
            group, artifact, version = parts
            classifier = None
        elif len(parts) == 4:
            group, artifact, version, classifier = parts
        else:
            raise LibraryError(f"Maven 坐标段数不对（应为 3 或 4 段）：{name!r}")
        if not (group and artifact and version):
            raise LibraryError(f"Maven 坐标有空字段：{name!r}")
        return cls(
            group=group,
            artifact=artifact,
            version=version,
            classifier=classifier or None,
            extension=extension or "jar",
        )

    @property
    def file_name(self) -> str:
        suffix = f"-{self.classifier}" if self.classifier else ""
        return f"{self.artifact}-{self.version}{suffix}.{self.extension}"

    @property
    def path(self) -> str:
        """相对 ``libraries/`` 的 POSIX 路径。"""
        return "/".join(
            (
                self.group.replace(".", "/"),
                self.artifact,
                self.version,
                self.file_name,
            )
        )


def coordinate(library: Library) -> MavenCoordinate:
    return MavenCoordinate.parse(library.name)


def coordinate_ok(library: Library) -> bool:
    """名字能不能当成 Maven 坐标用（有些老版本会放非标准名字）。"""
    try:
        coordinate(library)
    except LibraryError:
        return False
    return True


def is_native_library(library: Library) -> bool:
    """该库是不是"只解压、不进 classpath"的 natives 库。

    两种形态：
    - 新格式（1.19+）：坐标自带 ``natives-windows`` 之类的 classifier；
    - 老格式：带 ``natives`` 映射，另有 ``downloads.classifiers``。
    """
    if library.natives:
        return True
    classifier = coordinate(library).classifier
    return bool(classifier and classifier.startswith("natives-"))


def synthetic_artifact(library: Library) -> Artifact | None:
    """有些 JSON（特别是 Fabric 官方生成的那种）只给 ``url`` + Maven 坐标，
    没有 ``downloads.artifact``。这里按坐标补出一个下载物。"""
    if library.artifact is not None and library.artifact.path:
        return library.artifact
    try:
        coord = coordinate(library)
    except LibraryError:
        return None
    path = coord.path
    url = f"{library.url.rstrip('/')}/{path}" if library.url else None
    return Artifact(path=path, url=url)


def native_artifact(library: Library, env: Environment) -> tuple[Artifact | None, str | None]:
    """返回 (natives 压缩包, 坐标里的 classifier)。

    找不到对应平台的 natives 时返回 ``(None, None)``。
    """
    coord = coordinate(library)
    # 新格式：classifier 直接写在坐标里，artifact 就是那个 natives jar
    if coord.classifier and coord.classifier.startswith("natives-"):
        return library.artifact or synthetic_artifact(library), coord.classifier
    # 老格式
    raw = library.natives.get(env.os_name if env.os_name != "osx" else "osx")
    if not raw:
        return None, None
    classifier = (
        raw.replace("${arch}", "64" if env.arch in ("x86_64", "arm64") else "32")
        .replace("${arch64}", "64" if env.arch in ("x86_64", "arm64") else "32")
    )
    return library.classifiers.get(classifier), classifier


def effective_libraries(
    version: VersionJson, env: Environment | None = None
) -> tuple[tuple[Library, ...], tuple[Library, ...]]:
    """按规则过滤后返回 (要进 classpath 的库, 要解压的 natives 库)。"""
    env = env or Environment()
    classpath: list[Library] = []
    natives: list[Library] = []
    for library in version.libraries:
        if not allow(library.rules, env):
            continue
        if is_native_library(library):
            artifact, _classifier = native_artifact(library, env)
            if artifact is not None:
                natives.append(library)
            # natives 库只解压、不进 classpath。老格式里那个"普通 jar"
            # 通常只是个壳（本地根本不存在），官方启动器也不依赖它。
            continue
        if library.artifact is not None or coordinate_ok(library):
            classpath.append(library)
    return tuple(classpath), tuple(natives)


def library_relative_path(library: Library) -> str:
    """库文件相对 ``libraries/`` 的路径：优先用 JSON 里写好的 path。"""
    if library.artifact is not None and library.artifact.path:
        return library.artifact.path.replace("\\", "/")
    return coordinate(library).path


def artifact_relative_path(library: Library, artifact: Artifact) -> str:
    """任意下载物（含 natives）相对 ``libraries/`` 的路径。"""
    if artifact.path:
        return artifact.path.replace("\\", "/")
    coord = coordinate(library)
    if coord.classifier:
        return coord.path
    # 老格式的 natives classifier：名字里带 -natives-windows
    for classifier, candidate in library.classifiers.items():
        if candidate is artifact:
            return MavenCoordinate(
                group=coord.group,
                artifact=coord.artifact,
                version=coord.version,
                classifier=classifier,
                extension=coord.extension,
            ).path
    return coord.path


def native_file_name(library: Library, artifact: Artifact) -> str:
    """natives 压缩包下载到本地的文件名。"""
    if artifact.path:
        return artifact.path.rsplit("/", 1)[-1]
    return coordinate(library).file_name


def natives_dir_name(env: Environment) -> str:
    """natives 解压目录名，保持 HMCL 的习惯：natives-windows-x86_64。"""
    return f"natives-{env.os_name}-{env.arch}"


def download_url(library: Library, artifact: Artifact, default_base: str | None = None) -> str | None:
    """取下载地址：artifact.url → 库上的备用仓库 → 默认源。"""
    if artifact.url:
        return artifact.url
    base = library.url or default_base
    if not base:
        return None
    return base.rstrip("/") + "/" + (artifact.path or coordinate(library).path)


def library_paths(libraries: Iterable[Library]) -> list[str]:
    return [library_relative_path(lib) for lib in libraries]
