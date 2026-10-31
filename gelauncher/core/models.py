"""版本 JSON 的数据模型。

设计原则（见方案 §10）：
- 用 dataclass 把 JSON 映射成对象，不在业务代码里到处 ``d["x"]``；
- 字段缺失时抛出带上下文的 ``VersionJsonError``，而不是给出 ``None`` 然后静默崩掉；
- 对未知字段一律忽略：Mojang 和加载器都会往里加东西，严格模式只会自找麻烦。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class VersionJsonError(ValueError):
    """版本 JSON 结构不符合预期。message 里一定带出现问题的位置。"""


def _require(mapping: Mapping[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise VersionJsonError(f"{ctx}: 缺少必填字段 {key!r}")
    return mapping[key]


def _optional_str(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def _optional_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise VersionJsonError(f"字段 {key!r} 应该是整数，实际是 {value!r}")
    return value


def _as_mapping(value: Any, ctx: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VersionJsonError(f"{ctx}: 期望一个对象，实际是 {type(value).__name__}")
    return value


# --------------------------------------------------------------------------- #
# 规则
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    """一条 allow/deny 规则，对应 JSON 里的 ``rules[]`` 元素。

    没有写出来的条件表示"不限"，写了就必须全部匹配。
    """

    action: str = "allow"
    os_name: str | None = None
    os_arch: str | None = None
    os_version: str | None = None
    features: Mapping[str, bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str = "rules[]") -> "Rule":
        data = _as_mapping(data, ctx)
        action = data.get("action", "allow")
        if action == "disallow":  # 1.12 时代的老写法
            action = "deny"
        if action not in ("allow", "deny"):
            raise VersionJsonError(f"{ctx}: action 只能是 allow/deny，实际是 {action!r}")
        os_data = data.get("os") or {}
        if not isinstance(os_data, Mapping):
            raise VersionJsonError(f"{ctx}: os 字段应该是对象")
        features = data.get("features") or {}
        if not isinstance(features, Mapping):
            raise VersionJsonError(f"{ctx}: features 字段应该是对象")
        return cls(
            action=action,
            os_name=os_data.get("name"),
            os_arch=os_data.get("arch"),
            os_version=os_data.get("version"),
            features={str(k): bool(v) for k, v in features.items()},
        )


def parse_rules(raw: Any, ctx: str = "rules[]") -> tuple[Rule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise VersionJsonError(f"{ctx}: rules 应该是数组")
    return tuple(Rule.from_dict(item, f"{ctx}[{i}]") for i, item in enumerate(raw))


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ConditionalArg:
    """``arguments`` 里带 rules 的元素。value 可以是一个字符串或字符串数组。"""

    value: tuple[str, ...]
    rules: tuple[Rule, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str) -> "ConditionalArg":
        raw = data.get("value")
        if raw is None:
            raise VersionJsonError(f"{ctx}: 带 rules 的参数缺少 value")
        if isinstance(raw, str):
            values: tuple[str, ...] = (raw,)
        elif isinstance(raw, Sequence) and not isinstance(raw, (bytes,)):
            values = tuple(str(v) for v in raw)
        else:
            raise VersionJsonError(f"{ctx}: value 应该是字符串或字符串数组")
        return cls(value=values, rules=parse_rules(data.get("rules"), f"{ctx}.rules"))


Argument = str | ConditionalArg


def parse_arguments(raw: Any, ctx: str = "arguments") -> tuple[Argument, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise VersionJsonError(f"{ctx}: 应该是数组")
    result: list[Argument] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(ConditionalArg.from_dict(item, f"{ctx}[{i}]"))
        else:
            raise VersionJsonError(
                f"{ctx}[{i}]: 参数只能是字符串或对象，实际是 {type(item).__name__}"
            )
    return tuple(result)


# --------------------------------------------------------------------------- #
# 下载物
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Artifact:
    """一个可下载文件：路径 + 地址 + 校验信息。"""

    path: str
    url: str | None = None
    sha1: str | None = None
    size: int | None = None

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], ctx: str = "artifact", require_path: bool = True
    ) -> "Artifact":
        data = _as_mapping(data, ctx)
        path = data.get("path")
        if require_path:
            if not isinstance(path, str) or not path:
                raise VersionJsonError(f"{ctx}: 缺少必填字段 'path'")
        elif not isinstance(path, str):
            path = ""
        url = data.get("url")
        sha1 = data.get("sha1")
        if url is not None and not isinstance(url, str):
            raise VersionJsonError(f"{ctx}: url 应该是字符串")
        return cls(
            path=path,
            url=url,
            sha1=sha1 if isinstance(sha1, str) else None,
            size=_optional_int(data, "size"),
        )


# --------------------------------------------------------------------------- #
# 库
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Library:
    """``libraries[]`` 的一个元素。"""

    name: str
    artifact: Artifact | None = None
    classifiers: Mapping[str, Artifact] = field(default_factory=dict)
    # 老格式：{"windows": "natives-windows-${arch}"}
    natives: Mapping[str, str] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
    extract_exclude: tuple[str, ...] = ()
    # 老版本给库配的备用仓库前缀，Maven 坐标拼接时用得上
    url: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str = "libraries[]") -> "Library":
        data = _as_mapping(data, ctx)
        name = _require(data, "name", ctx)
        if not isinstance(name, str) or not name:
            raise VersionJsonError(f"{ctx}: name 必须是非空字符串")

        artifact_raw = data.get("artifact")
        if artifact_raw is None:
            downloads = data.get("downloads") or {}
            if not isinstance(downloads, Mapping):
                raise VersionJsonError(f"{ctx}: downloads 应该是对象")
            artifact_raw = downloads.get("artifact")
        artifact = (
            Artifact.from_dict(
                artifact_raw, f"{ctx}.artifact", require_path=False
            )
            if artifact_raw is not None
            else None
        )

        classifiers: dict[str, Artifact] = {}
        downloads = data.get("downloads") or {}
        if isinstance(downloads, Mapping):
            raw_classifiers = downloads.get("classifiers") or {}
            if not isinstance(raw_classifiers, Mapping):
                raise VersionJsonError(f"{ctx}: downloads.classifiers 应该是对象")
            for key, value in raw_classifiers.items():
                classifiers[str(key)] = Artifact.from_dict(
                    value, f"{ctx}.downloads.classifiers[{key}]", require_path=False
                )

        natives_raw = data.get("natives") or {}
        if not isinstance(natives_raw, Mapping):
            raise VersionJsonError(f"{ctx}: natives 应该是对象")
        natives = {str(k): str(v) for k, v in natives_raw.items()}

        extract = data.get("extract") or {}
        if not isinstance(extract, Mapping):
            raise VersionJsonError(f"{ctx}: extract 应该是对象")
        exclude_raw = extract.get("exclude") or []
        if not isinstance(exclude_raw, Sequence) or isinstance(exclude_raw, (str, bytes)):
            raise VersionJsonError(f"{ctx}: extract.exclude 应该是数组")

        return cls(
            name=name,
            artifact=artifact,
            classifiers=classifiers,
            natives=natives,
            rules=parse_rules(data.get("rules"), f"{ctx}.rules"),
            extract_exclude=tuple(str(x) for x in exclude_raw),
            url=_optional_str(data, "url"),
        )


# --------------------------------------------------------------------------- #
# 资源 / 日志 / Java
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AssetIndex:
    id: str
    url: str | None = None
    sha1: str | None = None
    size: int | None = None
    total_size: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str = "assetIndex") -> "AssetIndex":
        data = _as_mapping(data, ctx)
        return cls(
            id=str(_require(data, "id", ctx)),
            url=_optional_str(data, "url"),
            sha1=_optional_str(data, "sha1"),
            size=_optional_int(data, "size"),
            total_size=_optional_int(data, "totalSize"),
        )


@dataclass(frozen=True)
class DownloadFile:
    id: str
    url: str | None = None
    sha1: str | None = None
    size: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str = "file") -> "DownloadFile":
        data = _as_mapping(data, ctx)
        return cls(
            id=str(_require(data, "id", ctx)),
            url=_optional_str(data, "url"),
            sha1=_optional_str(data, "sha1"),
            size=_optional_int(data, "size"),
        )


@dataclass(frozen=True)
class LoggingClient:
    file: DownloadFile
    argument: str = "-Dlog4j.configurationFile=${path}"
    type: str = "log4j2-xml"


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VersionJson:
    id: str
    main_class: str | None = None
    jvm_arguments: tuple[Argument, ...] = ()
    game_arguments: tuple[Argument, ...] = ()
    minecraft_arguments: str | None = None
    libraries: tuple[Library, ...] = ()
    asset_index: AssetIndex | None = None
    assets: str | None = None
    downloads: Mapping[str, Artifact] = field(default_factory=dict)
    logging: LoggingClient | None = None
    java_version: int | None = None
    inherits_from: str | None = None
    type: str | None = None
    # 该版本实际使用的 jar id，缺省等于 id
    jar: str | None = None
    release_time: str | None = None

    # ---------------------------------------------------------------- 解析

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], ctx: str = "version") -> "VersionJson":
        data = _as_mapping(data, ctx)
        version_id = _require(data, "id", ctx)
        if not isinstance(version_id, str):
            raise VersionJsonError(f"{ctx}: id 应该是字符串")

        arguments = data.get("arguments")
        jvm_arguments: tuple[Argument, ...] = ()
        game_arguments: tuple[Argument, ...] = ()
        if arguments is not None:
            arguments = _as_mapping(arguments, f"{ctx}.arguments")
            jvm_arguments = parse_arguments(arguments.get("jvm"), f"{ctx}.arguments.jvm")
            game_arguments = parse_arguments(arguments.get("game"), f"{ctx}.arguments.game")

        minecraft_arguments = data.get("minecraftArguments")
        if minecraft_arguments is not None and not isinstance(minecraft_arguments, str):
            raise VersionJsonError(f"{ctx}: minecraftArguments 应该是字符串")

        raw_libraries = data.get("libraries") or []
        if not isinstance(raw_libraries, Sequence) or isinstance(raw_libraries, (str, bytes)):
            raise VersionJsonError(f"{ctx}: libraries 应该是数组")
        libraries = tuple(
            Library.from_dict(item, f"{ctx}.libraries[{i}]")
            for i, item in enumerate(raw_libraries)
        )

        downloads: dict[str, Artifact] = {}
        raw_downloads = data.get("downloads") or {}
        if not isinstance(raw_downloads, Mapping):
            raise VersionJsonError(f"{ctx}: downloads 应该是对象")
        for key, value in raw_downloads.items():
            if isinstance(value, Mapping):
                # 版本级 downloads 没有 path 字段（路径由版本目录约定决定）
                downloads[str(key)] = Artifact.from_dict(
                    value, f"{ctx}.downloads[{key}]", require_path=False
                )

        logging_client: LoggingClient | None = None
        raw_logging = (data.get("logging") or {}).get("client")
        if raw_logging is not None:
            raw_logging = _as_mapping(raw_logging, f"{ctx}.logging.client")
            logging_client = LoggingClient(
                file=DownloadFile.from_dict(
                    _require(raw_logging, "file", f"{ctx}.logging.client"),
                    f"{ctx}.logging.client.file",
                ),
                argument=str(
                    raw_logging.get("argument", "-Dlog4j.configurationFile=${path}")
                ),
                type=str(raw_logging.get("type", "log4j2-xml")),
            )

        asset_index = None
        if data.get("assetIndex") is not None:
            asset_index = AssetIndex.from_dict(data["assetIndex"], f"{ctx}.assetIndex")

        java_version = None
        raw_java = data.get("javaVersion")
        if raw_java is not None:
            raw_java = _as_mapping(raw_java, f"{ctx}.javaVersion")
            java_version = _optional_int(raw_java, "majorVersion")

        return cls(
            id=version_id,
            main_class=_optional_str(data, "mainClass"),
            jvm_arguments=jvm_arguments,
            game_arguments=game_arguments,
            minecraft_arguments=minecraft_arguments,
            libraries=libraries,
            asset_index=asset_index,
            assets=_optional_str(data, "assets"),
            downloads=downloads,
            logging=logging_client,
            java_version=java_version,
            inherits_from=_optional_str(data, "inheritsFrom"),
            type=_optional_str(data, "type"),
            jar=_optional_str(data, "jar"),
            release_time=_optional_str(data, "releaseTime"),
        )

    @classmethod
    def load(cls, path: Path) -> "VersionJson":
        path = Path(path)
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise VersionJsonError(f"找不到版本 JSON：{path}") from None
        except json.JSONDecodeError as exc:
            raise VersionJsonError(f"版本 JSON 解析失败（{path}）：{exc}") from exc
        return cls.from_dict(data, f"version({path.name})")

    # ---------------------------------------------------------------- 便捷

    def library_names(self) -> Iterable[str]:
        return (lib.name for lib in self.libraries)

    def describe(self) -> str:
        """一行摘要，CLI/日志里用。"""
        return (
            f"{self.id} (mainClass={self.main_class or '?'}, "
            f"libs={len(self.libraries)}, java={self.java_version or '?'}"
            f"{', inherits=' + self.inherits_from if self.inherits_from else ''})"
        )
