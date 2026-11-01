"""把版本 JSON 变成一条真实的 java 命令，并把游戏跑起来。

数据流（方案 §4.1）：
    读 JSON → 合并 inheritsFrom → 过滤 rules → 分离普通库/natives
    → 解压 natives → 拼 classpath → 替换占位符 → subprocess.Popen
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, TextIO

from gelauncher import LAUNCHER_NAME, __version__

from . import inherit, libraries
from .command import (
    Account,
    CommandError,
    Placeholders,
    build_placeholders,
    expand_arguments,
    legacy_game_arguments,
    legacy_jvm_arguments,
)
from .java import JavaError, JavaRuntime, find_java
from .layout import GameLayout, current_env
from .logfmt import GameOutputFormatter
from .models import VersionJson
from .natives import NativesError, extract_natives
from .rules import Environment


class LaunchError(RuntimeError):
    """启动前的检查没过。message 是给用户看的人话。"""


@dataclass(frozen=True)
class LaunchOptions:
    layout: GameLayout
    version_id: str
    account: Account = field(default_factory=Account)
    java_path: Path | None = None
    max_memory_mb: int = 2048
    min_memory_mb: int | None = None
    width: int | None = None
    height: int | None = None
    extra_jvm_args: tuple[str, ...] = ()
    extra_game_args: tuple[str, ...] = ()
    # 用户要求"一律隔离"：gameDir = versions/<id>
    isolated: bool = True
    env: Environment | None = None
    force_natives: bool = False
    #: 只做检查、不产生任何副作用（不建目录、不解压 natives）
    dry_run: bool = False


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    cwd: Path
    java: JavaRuntime
    version: VersionJson
    version_id: str
    classpath: tuple[Path, ...]
    natives_dir: Path
    log_config: Path | None = None
    warnings: tuple[str, ...] = ()

    def printable_command(self) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(self.command)
        return " ".join(self.command)


def _natives_target_dir(version: VersionJson, base_dir: Path) -> Path:
    """决定 natives 解压到哪个目录（26.x 起需要放进 ``<natives>/java`` 子目录）。

    1.21 及更早：``-Djava.library.path=${natives_directory}``，解到 base 就行。
    26.3 开始：``-Djava.library.path=${natives_directory}/java``，natives jar 内部也变成
    ``windows/x64/org/lwjgl/...`` 这种嵌套结构。这时必须解到 ``base/java``，
    否则 java.library.path 指向不存在的目录，游戏报
    ``Failed to locate library: lwjgl.dll``（实测 26.3 就是这个问题）。
    """
    prefix = "-Djava.library.path=${natives_directory}/"
    for argument in version.jvm_arguments:
        if not isinstance(argument, str) or not argument.startswith(prefix):
            continue
        sub = argument[len(prefix) :].split("${", 1)[0].strip("/\\")
        if not sub or ".." in Path(sub).parts or Path(sub).is_absolute():
            break
        return base_dir / sub
    return base_dir


def _resolve_jar(layout: GameLayout, version: VersionJson, version_id: str) -> Path | None:
    """找游戏 jar：先看实例自己的，再顺着 inheritsFrom 找原版的。

    实例名可以和版本号不同（比如实例 "生存服" 用的是 1.21.8），
    老实例 / 别的启动器留下的目录也可能把 jar 放在父版本里。
    """
    candidates = [version.jar or version_id, version_id]
    for name in candidates:
        path = layout.version_jar_path(name)
        if path.is_file():
            return path
    probe = version.inherits_from
    seen: set[str] = set()
    while probe and probe not in seen:
        seen.add(probe)
        path = layout.version_jar_path(probe)
        if path.is_file():
            return path
        try:
            parent = layout.load_json(probe)
        except VersionJsonError:
            return None
        if parent.jar:
            path = layout.version_jar_path(parent.jar)
            if path.is_file():
                return path
        probe = parent.inherits_from
    return None


def _missing(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if not path.is_file()]


def _check_files(paths: Iterable[Path], what: str, hint: str) -> None:
    missing = _missing(paths)
    if not missing:
        return
    shown = "\n".join(f"  - {path}" for path in missing[:8])
    more = f"\n  ...（还有 {len(missing) - 8} 个）" if len(missing) > 8 else ""
    raise LaunchError(f"{what} 缺 {len(missing)} 个文件：\n{shown}{more}\n{hint}")


def prepare(options: LaunchOptions) -> LaunchPlan:
    """做完所有检查，返回一条可以直接执行的命令。"""
    layout = options.layout
    version_id = options.version_id
    if not layout.version_json_path(version_id).is_file():
        raise LaunchError(
            f"版本 {version_id!r} 没有安装：找不到 {layout.version_json_path(version_id)}\n"
            f"已经装好的版本：{', '.join(layout.installed_versions()) or '（无）'}"
        )

    version = inherit.resolve(layout, version_id)
    env = options.env or current_env()
    warnings: list[str] = []

    if not version.main_class:
        raise LaunchError(f"版本 {version_id} 的 JSON 里没有 mainClass，无法启动")

    # ---- 库 ----
    classpath_libs, native_libs = libraries.effective_libraries(version, env)
    classpath: list[Path] = [layout.libraries_dir / libraries.library_relative_path(lib) for lib in classpath_libs]
    native_jars: list[Path] = []
    native_excludes: list[str] = []
    for library in native_libs:
        artifact, classifier = libraries.native_artifact(library, env)
        assert artifact is not None  # effective_libraries 已经过滤过
        native_jars.append(
            layout.libraries_dir / libraries.artifact_relative_path(library, artifact)
        )
        native_excludes.extend(library.extract_exclude)
    _check_files(
        classpath + native_jars,
        "库文件",
        "这个版本没装完整，先用启动器的安装功能补全（或换 HMCL/PCL2 装一次）。",
    )

    # ---- 游戏 jar ----
    jar_path = _resolve_jar(layout, version, version_id)
    if jar_path is None:
        raise LaunchError(
            f"找不到游戏 jar：{layout.version_jar_path(version.jar or version_id)}\n"
            "Fabric/Forge 这类版本需要先装好对应的原版，或者版本目录里的 jar 被删了。"
        )
    classpath.append(jar_path)

    # ---- 资源 ----
    assets_root = layout.assets_dir
    if version.asset_index is not None:
        assets_index_name = version.asset_index.id
        index_path = layout.asset_index_path(assets_index_name)
        if not index_path.is_file():
            raise LaunchError(
                f"找不到资源索引：{index_path}\n"
                "缺少 assets 会导致游戏启动后没有声音/贴图，先用安装功能下载资源。"
            )
    else:
        assets_index_name = version.assets or "legacy"
        if not (assets_root / "indexes" / f"{assets_index_name}.json").is_file():
            warnings.append(f"没有 assetIndex，也不存在 assets/indexes/{assets_index_name}.json")

    # ---- 日志配置 ----
    log_config: Path | None = None
    if version.logging is not None:
        candidate = layout.log_config_path(version.logging.file.id)
        if candidate.is_file():
            log_config = candidate
        else:
            # HMCL/PCL2 习惯把 log4j2.xml 放在版本目录里，兜底找一下
            fallback = layout.version_dir(version_id) / "log4j2.xml"
            if fallback.is_file():
                log_config = fallback
            else:
                warnings.append(
                    f"缺少日志配置 assets/log_configs/{version.logging.file.id}，本次不传 -Dlog4j.configurationFile"
                )

    # ---- 路径 ----
    game_directory = layout.game_dir(version_id, isolated=options.isolated)
    if not options.dry_run:
        game_directory.mkdir(parents=True, exist_ok=True)
    natives_dir = layout.natives_dir(version_id, env)
    natives_target = _natives_target_dir(version, natives_dir)
    if not options.dry_run:
        try:
            extract_natives(
                native_jars, natives_target, native_excludes, force=options.force_natives
            )
        except (NativesError, OSError) as exc:
            raise LaunchError(f"解压 natives 失败：{exc}") from exc

    # ---- Java ----
    try:
        java = find_java(version.java_version, options.java_path)
    except JavaError as exc:
        raise LaunchError(str(exc)) from exc
    if version.java_version and java.major < version.java_version:
        warnings.append(
            f"这个版本建议 Java {version.java_version}，当前是 Java {java.major}，可能起不来"
        )

    # ---- 占位符 ----
    classpath_text = os.pathsep.join(str(path.resolve()) for path in classpath)
    placeholders = build_placeholders(
        version=version,
        version_name=version_id,
        account=options.account,
        game_directory=str(game_directory.resolve()),
        assets_root=str(assets_root.resolve()),
        assets_index_name=assets_index_name,
        natives_directory=str(natives_dir.resolve()),
        library_directory=str(layout.libraries_dir.resolve()),
        classpath=classpath_text,
        primary_jar_name=jar_path.name,
        launcher_name=LAUNCHER_NAME,
        launcher_version=__version__,
        version_type=version.type or "release",
        log_config_path=str(log_config.resolve()) if log_config else None,
        resolution=(options.width, options.height)
        if options.width and options.height
        else None,
    )
    features = dict(env.features or {})
    if options.width and options.height:
        features["has_custom_resolution"] = True
    env = Environment(
        os_name=env.os_name,
        arch=env.arch,
        os_version=env.os_version,
        features=features,
    )

    try:
        if version.jvm_arguments:
            jvm_args = expand_arguments(version.jvm_arguments, placeholders, env)
        else:
            jvm_args = expand_arguments(
                [str(item) for item in legacy_jvm_arguments(placeholders)], placeholders, env
            )
        if version.game_arguments:
            game_args = expand_arguments(version.game_arguments, placeholders, env)
        elif version.minecraft_arguments:
            game_args = expand_arguments(
                legacy_game_arguments(version.minecraft_arguments), placeholders, env
            )
        else:
            raise LaunchError("版本 JSON 里既没有 arguments.game 也没有 minecraftArguments")
    except CommandError as exc:
        raise LaunchError(str(exc)) from exc

    # ---- 拼命令 ----
    command: list[str] = [str(java.path)]
    command.append(f"-Xmx{options.max_memory_mb}M")
    if options.min_memory_mb:
        command.append(f"-Xms{options.min_memory_mb}M")
    # 中文路径 / 中文日志不乱码（方案 §7）
    command.extend(["-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8"])
    command.extend(jvm_args)
    if log_config is not None and version.logging is not None:
        command.append(
            version.logging.argument.replace("${path}", str(log_config.resolve()))
        )
    command.extend(options.extra_jvm_args)
    command.append(version.main_class)
    command.extend(game_args)
    command.extend(options.extra_game_args)

    return LaunchPlan(
        command=command,
        cwd=game_directory,
        java=java,
        version=version,
        version_id=version_id,
        classpath=tuple(classpath),
        natives_dir=natives_dir,
        log_config=log_config,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #


@dataclass
class LaunchResult:
    returncode: int
    log_path: Path | None = None


def _pump(stream: TextIO, sink: TextIO | None, on_line: Callable[[str], None] | None) -> None:
    formatter = GameOutputFormatter()

    def emit(text: str) -> None:
        if on_line is not None:
            on_line(text)
        if sink is not None:
            sink.write(text + "\n")
            sink.flush()

    try:
        for line in stream:
            text = formatter.feed(line.rstrip("\r\n"))
            if text is not None:
                emit(text)
        for leftover in formatter.flush():
            emit(leftover)
    except (ValueError, OSError):
        pass  # 进程退出时管道关掉是正常的


def run(
    plan: LaunchPlan,
    *,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    wait: bool = True,
) -> LaunchResult | subprocess.Popen:
    """启动游戏。``wait=True`` 时等到退出并返回退出码。"""
    sink: TextIO | None = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        sink = Path(log_path).open("w", encoding="utf-8", errors="replace")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            plan.command,
            cwd=str(plan.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except OSError as exc:
        if sink is not None:
            sink.close()
        raise LaunchError(f"启动 java 失败：{exc}") from exc

    assert process.stdout is not None
    pump = threading.Thread(
        target=_pump, args=(process.stdout, sink, on_line), daemon=True
    )
    pump.start()

    if not wait:
        return process

    returncode = process.wait()
    pump.join(timeout=5)
    if sink is not None:
        sink.close()
    return LaunchResult(returncode=returncode, log_path=Path(log_path) if log_path else None)
