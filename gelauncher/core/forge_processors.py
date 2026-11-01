"""Forge / NeoForge 的 processors 安装流程（移植自 HMCL 的 ForgeNewInstallTask）。

老的 Forge / NeoForge installer 只支持 ``--installServer``，没有 ``--installClient``，
没法用命令行装客户端。HMCL 的做法是直接读 installer 里的 ``install_profile.json``：

1. 把 installer 内嵌的 ``maven/`` 文件和 install_profile 里列的库准备好；
2. 按 profile 的 ``data`` + 运行时变量（SIDE / MINECRAFT_JAR / ROOT / ...）展开占位符；
3. 依次执行 ``processors``（每个就是一个带 main-class 的 jar），产物写到 libraries 里；
4. 最后把 installer 里的 ``version.json`` 合并进实例。

这里只做"跑处理器 + 产出 patch"，合并交给 ``install_loader._merge_loader_json``。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .layout import GameLayout
from .net import DownloadJob, DownloadReport, Fetcher

ProgressHook = Callable[[str, str], None]


class ProcessorError(RuntimeError):
    """processors 安装失败。"""


@dataclass
class InstallProfile:
    minecraft: str
    version: str
    json_entry: str
    data: dict[str, dict[str, str]]
    processors: list[dict]
    libraries: list[dict]
    version_json: dict


# --------------------------------------------------------------------------- #
# maven 坐标
# --------------------------------------------------------------------------- #


def _split_descriptor(text: str) -> tuple[str, str, str, str | None, str | None]:
    """``group:artifact:version[:classifier][@ext]`` → 五元组。"""
    ext: str | None = None
    if "@" in text:
        text, ext = text.rsplit("@", 1)
    parts = text.split(":")
    if len(parts) < 3:
        raise ProcessorError(f"看不懂的 maven 坐标：{text}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) >= 4 else None
    return group, artifact, version, classifier, ext


def artifact_relative_path(descriptor: str) -> str:
    group, artifact, version, classifier, ext = _split_descriptor(descriptor)
    name = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext or 'jar'}"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{name}"


def artifact_path(layout: GameLayout, descriptor: str) -> Path:
    return layout.libraries_dir / artifact_relative_path(descriptor)


# --------------------------------------------------------------------------- #
# 占位符
# --------------------------------------------------------------------------- #


def replace_tokens(vars: dict[str, str], value: str) -> str:
    """Forge 的占位符语法：``{KEY}``、``'literal'``、反斜杠转义。"""
    out: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\":
            index += 1
            if index >= length:
                raise ProcessorError(f"占位符转义不完整：{value}")
            out.append(value[index])
            index += 1
        elif char in "{'":
            quote = char
            key: list[str] = []
            index += 1
            closed = False
            while index < length:
                inner = value[index]
                if inner == "\\":
                    index += 1
                    if index >= length:
                        raise ProcessorError(f"占位符转义不完整：{value}")
                    key.append(value[index])
                    index += 1
                    continue
                if quote == "{" and inner == "}":
                    closed = True
                    index += 1
                    break
                if quote == "'" and inner == "'":
                    closed = True
                    index += 1
                    break
                key.append(inner)
                index += 1
            if not closed:
                raise ProcessorError(f"占位符没有闭合：{value}")
            text = "".join(key)
            if quote == "'":
                out.append(text)
            else:
                if text not in vars:
                    raise ProcessorError(f"未知的安装变量 {{{text}}}")
                out.append(vars[text])
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _parse_literal(
    literal: str,
    vars: dict[str, str],
    layout: GameLayout,
    extract,
) -> str:
    if literal.startswith("{") and literal.endswith("}") and literal.count("{") == 1:
        key = literal[1:-1]
        if key not in vars:
            raise ProcessorError(f"未知的安装变量 {{{key}}}")
        return vars[key]
    if literal.startswith("'") and literal.endswith("'") and literal.count("'") == 2:
        return literal[1:-1]
    if literal.startswith("[") and literal.endswith("]"):
        return str(artifact_path(layout, literal[1:-1]))
    text = replace_tokens(vars, literal)
    if text.startswith("/") or text.startswith("\\"):
        return str(extract(text))
    return text


# --------------------------------------------------------------------------- #
# 读 profile
# --------------------------------------------------------------------------- #


def read_profile(installer: Path) -> InstallProfile:
    with zipfile.ZipFile(installer) as archive:
        try:
            raw = json.loads(archive.read("install_profile.json").decode("utf-8"))
        except KeyError as exc:
            raise ProcessorError("installer 里没有 install_profile.json") from exc
        json_entry = str(raw.get("json") or "/version.json").lstrip("/")
        try:
            version_json = json.loads(archive.read(json_entry).decode("utf-8"))
        except KeyError:
            version_json = json.loads(raw.get("versionInfo") or "{}")
    if not version_json:
        raise ProcessorError("installer 里没有版本 JSON（既没有 json 也没有 versionInfo）")
    return InstallProfile(
        minecraft=str(raw.get("minecraft") or ""),
        version=str(raw.get("version") or ""),
        json_entry=json_entry,
        data=raw.get("data") or {},
        processors=raw.get("processors") or [],
        libraries=raw.get("libraries") or [],
        version_json=version_json,
    )


# --------------------------------------------------------------------------- #
# 准备文件
# --------------------------------------------------------------------------- #


def _copy_embedded_maven(installer: Path, layout: GameLayout) -> None:
    with zipfile.ZipFile(installer) as archive:
        for entry in archive.namelist():
            if not entry.startswith("maven/") or entry.endswith("/"):
                continue
            relative = entry[len("maven/") :]
            target = layout.libraries_dir / relative
            if target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)


def _download_profile_libraries(
    fetcher: Fetcher,
    layout: GameLayout,
    libraries: list[dict],
    *,
    progress: ProgressHook | None = None,
) -> DownloadReport:
    jobs: list[DownloadJob] = []
    for entry in libraries:
        artifact = (entry.get("downloads") or {}).get("artifact") or {}
        path = artifact.get("path")
        url = artifact.get("url")
        if not path or not url:
            continue
        destination = layout.libraries_dir / path
        if destination.is_file():
            continue
        urls = [url]
        if "maven.neoforged.net/" in url:
            urls.append(url.replace("https://maven.neoforged.net/", "https://neoforged.forgecdn.net/"))
        jobs.append(
            DownloadJob(
                urls=urls,
                destination=destination,
                sha1=artifact.get("sha1"),
                size=artifact.get("size"),
                label=path,
            )
        )
    if not jobs:
        return DownloadReport()
    if progress:
        progress("processors", f"下载 processor 需要的 {len(jobs)} 个库…")
    return fetcher.download_all(jobs)


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _main_class(jar: Path) -> str:
    with zipfile.ZipFile(jar) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    for line in manifest.splitlines():
        if line.lower().startswith("main-class:"):
            return line.split(":", 1)[1].strip()
    raise ProcessorError(f"processor jar 里没有 Main-Class：{jar.name}")


def _run_processor(
    java: Path,
    jar: Path,
    classpath: list[Path],
    args: list[str],
    *,
    cwd: Path,
    on_line: Callable[[str], None] | None = None,
) -> None:
    command = [str(java), "-cp", os.pathsep.join(str(p) for p in classpath + [jar]), _main_class(jar), *args]
    output: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None

    def pump() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            text = line.rstrip("\r\n")
            output.append(text)
            if on_line:
                on_line(text)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    code = process.wait()
    thread.join(timeout=5)
    if code != 0:
        raise ProcessorError(
            f"processor 失败（退出码 {code}）：{' '.join(args[:3])}\n" + "\n".join(output[-20:])
        )


def _download_mappings(
    fetcher: Fetcher,
    layout: GameLayout,
    output: Path,
    progress: ProgressHook | None,
) -> None:
    manifest = json.loads(fetcher.get_bytes(
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", label="manifest"
    ).decode("utf-8"))
    # 从 version json 里拿 client_mappings：调用方已经把具体 URL 放进了 data 变量里时走不到这里
    raise ProcessorError("DOWNLOAD_MOJMAPS 需要用版本 JSON 里的 client_mappings，暂未支持")


def install_with_processors(
    layout: GameLayout,
    instance: str,
    mc_version: str,
    installer: Path,
    *,
    fetcher: Fetcher,
    java: Path,
    progress: ProgressHook | None = None,
) -> dict:
    """跑完 processors，返回要合并进实例的版本 JSON（patch）。"""
    profile = read_profile(installer)
    if profile.minecraft and profile.minecraft != mc_version:
        raise ProcessorError(
            f"installer 是给 {profile.minecraft} 的，当前实例是 {mc_version}"
        )
    _copy_embedded_maven(installer, layout)
    report = _download_profile_libraries(fetcher, layout, profile.libraries, progress=progress)
    if not report.ok:
        raise ProcessorError(
            "processor 依赖下载失败："
            + "；".join(f"{name}：{reason}" for name, reason in report.failures[:5])
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="ge-forge-"))
    try:
        with zipfile.ZipFile(installer) as archive:
            extracted: dict[str, Path] = {}

            def extract(entry: str) -> Path:
                key = entry.lstrip("/")
                if key in extracted:
                    return extracted[key]
                try:
                    data = archive.read(key)
                except KeyError as exc:
                    raise ProcessorError(f"installer 里缺少文件：{key}") from exc
                target = temp_dir / Path(key).name
                target.write_bytes(data)
                extracted[key] = target
                return target

            vars: dict[str, str] = {}
            for key, value in profile.data.items():
                if isinstance(value, str):
                    vars[key] = _parse_literal(value, {}, layout, extract)
                elif isinstance(value, dict):
                    chosen = value.get("client") or next(iter(value.values()), "")
                    vars[key] = _parse_literal(str(chosen), {}, layout, extract)

        vanilla_jar = layout.version_jar_path(mc_version)
        if not vanilla_jar.is_file():
            raise ProcessorError(f"缺少原版客户端 jar：{vanilla_jar}")
        isolated_jar = temp_dir / "minecraft.jar"
        shutil.copy2(vanilla_jar, isolated_jar)

        vars.update(
            {
                "SIDE": "client",
                "MINECRAFT_JAR": str(isolated_jar.resolve()),
                "MINECRAFT_VERSION": mc_version,
                "ROOT": str(layout.root.resolve()),
                "INSTALLER": str(installer.resolve()),
                "LIBRARY_DIR": str(layout.libraries_dir.resolve()),
            }
        )

        processors = profile.processors
        for index, processor in enumerate(processors, start=1):
            sides = processor.get("sides") or []
            if sides and "client" not in sides:
                continue
            outputs = processor.get("outputs") or {}
            parsed_outputs: dict[Path, str] = {}
            missing = False
            for key, value in outputs.items():
                out_path = Path(_parse_literal(str(key), vars, layout, extract))
                expected = _parse_literal(str(value), vars, layout, extract)
                parsed_outputs[out_path] = expected
                if not out_path.is_file() or _sha1(out_path) != expected:
                    if out_path.is_file():
                        out_path.unlink(missing_ok=True)
                    missing = True
            if parsed_outputs and not missing:
                if progress:
                    progress("processors", f"{index}/{len(processors)} 已存在，跳过")
                continue

            if progress:
                progress("processors", f"{index}/{len(processors)} {processor.get('jar', '')}")
            jar_path = artifact_path(layout, str(processor.get("jar")))
            if not jar_path.is_file():
                raise ProcessorError(f"找不到 processor jar：{jar_path}")
            classpath = [artifact_path(layout, str(item)) for item in processor.get("classpath") or []]
            for item in classpath:
                if not item.is_file():
                    raise ProcessorError(f"processor 依赖缺文件：{item}")
            args = [_parse_literal(str(arg), vars, layout, extract) for arg in processor.get("args") or []]
            _run_processor(java, jar_path, classpath, args, cwd=temp_dir, on_line=None)

            for out_path, expected in parsed_outputs.items():
                if not out_path.is_file():
                    raise ProcessorError(f"processor 没有产出文件：{out_path}")
                actual = _sha1(out_path)
                if actual != expected:
                    raise ProcessorError(
                        f"processor 产出校验失败：{out_path.name}\n"
                        f"  期望 {expected}\n  实际 {actual}"
                    )

        patch = dict(profile.version_json)
        patch.setdefault("inheritsFrom", mc_version)
        return patch
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def missing_java_hint(java: Path) -> str:
    return f"运行 processor 需要 Java，当前用的是：{java}"
