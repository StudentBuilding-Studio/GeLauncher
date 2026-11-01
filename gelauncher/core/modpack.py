"""整合包导入：解析清单 → 建实例（原版 + 加载器）→ 下 mod/配置 → 解压覆盖文件。

支持的格式（按 HMCL 的检测顺序裁剪）：

- **MCBBS**：``mcbbs.packaging``，用 ``addons`` 决定版本/加载器，``overrides/`` 是文件
- **Modrinth**：``modrinth.index.json``（.mrpack），``files[].downloads`` 是直链
- **HMCL 导出包**：``modpack.json`` + ``minecraft/pack.json``，``minecraft/`` 是实例快照
- **手动整合包**：压缩包里直接是一个 ``.minecraft`` 结构（有 mods/config/versions 等）
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import install_loader, install_vanilla
from .layout import GameLayout
from .net import DownloadJob, DownloadReport, Fetcher

ProgressHook = "Callable[[str, str], None]"


class ModpackError(RuntimeError):
    """整合包读不了 / 信息不全。"""


@dataclass
class ModpackFile:
    path: str
    urls: list[str] = field(default_factory=list)
    sha1: str | None = None
    size: int | None = None


@dataclass
class ModpackInfo:
    provider: str
    name: str
    version: str
    game_version: str | None
    loader: str | None = None
    loader_version: str | None = None
    files: list[ModpackFile] = field(default_factory=list)
    #: 要解压进实例目录的 zip 前缀（例如 "overrides/"、"minecraft/"）
    overrides: list[str] = field(default_factory=list)
    #: 解压时跳过的条目（例如 "minecraft/pack.json"）
    skip: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)

    def summary(self) -> str:
        loader = f"{self.loader} {self.loader_version or ''}".strip() if self.loader else "原版"
        return f"{self.name} {self.version}｜{self.provider}｜MC {self.game_version or '?'}｜{loader}"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

_LOADER_ALIASES = {
    "fabric": "fabric",
    "fabric-loader": "fabric",
    "forge": "forge",
    "neoforge": "neoforge",
    "neo-forge": "neoforge",
}


def _read_json(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        raw = archive.read(name)
    except KeyError:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _modrinth(archive: zipfile.ZipFile) -> ModpackInfo | None:
    manifest = _read_json(archive, "modrinth.index.json")
    if manifest is None:
        return None
    dependencies = manifest.get("dependencies") or {}
    game_version = dependencies.get("minecraft")
    loader = None
    loader_version = None
    for key in ("fabric-loader", "forge", "neoforge", "neo-forge", "quilt-loader"):
        if key in dependencies:
            kind = _LOADER_ALIASES.get(key)
            if kind:
                loader = kind
                loader_version = str(dependencies[key])
            break
    info = ModpackInfo(
        provider="Modrinth",
        name=str(manifest.get("name") or "").strip() or "未命名整合包",
        version=str(manifest.get("versionId") or ""),
        game_version=str(game_version) if game_version else None,
        loader=loader,
        loader_version=loader_version,
        overrides=["overrides/", "client-overrides/"],
        manifest=manifest,
    )
    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict):
            continue
        env = entry.get("env") or {}
        if env.get("client") == "unsupported":
            continue
        path = entry.get("path")
        urls = [url for url in entry.get("downloads") or [] if isinstance(url, str)]
        if not isinstance(path, str) or not urls:
            continue
        hashes = entry.get("hashes") or {}
        info.files.append(
            ModpackFile(
                path=path,
                urls=urls,
                sha1=hashes.get("sha1"),
                size=entry.get("fileSize"),
            )
        )
    if "quilt-loader" in dependencies:
        info.notes.append("这个包用的是 Quilt 加载器，Ge 暂不支持，需要手动装")
        info.loader = None
    return info


def _hmcl(archive: zipfile.ZipFile) -> ModpackInfo | None:
    manifest = _read_json(archive, "modpack.json")
    pack = _read_json(archive, "minecraft/pack.json")
    if manifest is None or pack is None:
        return None
    jar = pack.get("jar") or {}
    game_version = (
        (jar.get("id") if isinstance(jar, dict) else None)
        or pack.get("clientVersion")
        or manifest.get("gameVersion")
        or pack.get("id")
    )
    loader = None
    loader_version = None
    for patch in pack.get("patches") or []:
        if not isinstance(patch, dict):
            continue
        kind = _LOADER_ALIASES.get(str(patch.get("id") or ""))
        if kind and patch.get("version"):
            loader = kind
            loader_version = str(patch["version"])
            break
    return ModpackInfo(
        provider="HMCL",
        name=str(manifest.get("name") or "").strip() or "未命名整合包",
        version=str(manifest.get("version") or ""),
        game_version=str(game_version) if game_version else None,
        loader=loader,
        loader_version=loader_version,
        overrides=["minecraft/"],
        skip={"minecraft/pack.json"},
        manifest={"modpack": manifest, "pack": pack},
    )


def _mcbbs(archive: zipfile.ZipFile) -> ModpackInfo | None:
    manifest = _read_json(archive, "mcbbs.packaging")
    if manifest is None or manifest.get("manifestType") != "minecraftModpack":
        return None
    game_version = None
    loader = None
    loader_version = None
    for addon in manifest.get("addons") or []:
        if not isinstance(addon, dict):
            continue
        addon_id = str(addon.get("id") or "").lower()
        version = str(addon.get("version") or "")
        if addon_id == "minecraft":
            game_version = version
            continue
        kind = _LOADER_ALIASES.get(addon_id)
        if kind and version:
            loader = kind
            loader_version = version
    info = ModpackInfo(
        provider="MCBBS",
        name=str(manifest.get("name") or "").strip() or "未命名整合包",
        version=str(manifest.get("version") or ""),
        game_version=game_version,
        loader=loader,
        loader_version=loader_version,
        overrides=["overrides/"],
        manifest=manifest,
    )
    file_api = str(manifest.get("fileApi") or "").rstrip("/")
    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "addon":
            info.notes.append("这个包含 CurseForge 文件，需要 CurseForge API 才能下载")
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        urls = [f"{file_api}/{path.lstrip('/')}"] if file_api else []
        info.files.append(
            ModpackFile(path=path.lstrip("/"), urls=urls, sha1=entry.get("hash"))
        )
    return info


_MC_DIR_MARKERS = ("mods", "config", "versions", "resourcepacks", "shaderpacks", "options.txt", "saves")


def _manual_root(archive: zipfile.ZipFile) -> str | None:
    names = archive.namelist()
    prefixes = [""]
    first = {name.split("/", 1)[0] for name in names if "/" in name}
    prefixes.extend(f"{entry}/" for entry in first)
    best: tuple[int, str] | None = None
    for prefix in prefixes:
        score = 0
        for marker in _MC_DIR_MARKERS:
            if any(name == prefix + marker or name.startswith(prefix + marker + "/") for name in names):
                score += 1
        if score >= 2 and (best is None or score > best[0]):
            best = (score, prefix)
    return best[1] if best else None


def _infer_from_version_json(archive: zipfile.ZipFile, prefix: str) -> tuple[str | None, str | None, str | None]:
    candidates = [
        name
        for name in archive.namelist()
        if name.startswith(prefix + "versions/") and name.endswith(".json") and name.count("/") == 2 + prefix.count("/")
    ]
    for name in candidates:
        data = _read_json(archive, name)
        if not data or not data.get("mainClass"):
            continue
        main = str(data["mainClass"]).lower()
        names = " ".join(str(lib.get("name", "")) for lib in data.get("libraries") or []).lower()
        loader = None
        loader_version = None
        if "fabric" in main or "net.fabricmc" in names:
            loader = "fabric"
            for lib in data.get("libraries") or []:
                if str(lib.get("name", "")).startswith("net.fabricmc:fabric-loader:"):
                    loader_version = str(lib["name"]).split(":")[2]
        elif "neoforge" in names or "net.neoforged" in names:
            loader = "neoforge"
            for lib in data.get("libraries") or []:
                if str(lib.get("name", "")).startswith("net.neoforged:neoforge:"):
                    loader_version = str(lib["name"]).split(":")[2]
        elif "forge" in main or "minecraftforge" in names:
            loader = "forge"
            for lib in data.get("libraries") or []:
                if str(lib.get("name", "")).startswith("net.minecraftforge:forge:"):
                    loader_version = str(lib["name"]).split(":")[2]
        base = data.get("clientVersion") or data.get("inheritsFrom") or data.get("jar") or data.get("id")
        return (str(base) if base else None, loader, loader_version)
    return (None, None, None)


def _manual(archive: zipfile.ZipFile) -> ModpackInfo | None:
    root = _manual_root(archive)
    if root is None:
        return None
    game_version, loader, loader_version = _infer_from_version_json(archive, root)
    info = ModpackInfo(
        provider="手动整合包",
        name="手动整合包",
        version="",
        game_version=game_version,
        loader=loader,
        loader_version=loader_version,
        overrides=[root] if root else [],
    )
    if game_version is None:
        info.notes.append("压缩包里没有版本 JSON，需要手动指定游戏版本")
    return info


_PROVIDERS = (_mcbbs, _modrinth, _hmcl, _manual)


def detect(archive_path: Path) -> ModpackInfo:
    """按顺序试各种格式，返回第一个能认出来的。"""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for provider in _PROVIDERS:
                try:
                    info = provider(archive)
                except (OSError, zipfile.BadZipFile):
                    raise
                except Exception:  # noqa: BLE001 - 单个格式解析失败就换下一个
                    continue
                if info is not None:
                    return info
    except zipfile.BadZipFile as exc:
        raise ModpackError(f"不是有效的 zip：{exc}") from exc
    raise ModpackError("认不出这个整合包格式（支持 .mrpack、MCBBS、HMCL 导出包、.minecraft 结构 zip）")


# --------------------------------------------------------------------------- #
# 安装
# --------------------------------------------------------------------------- #


@dataclass
class ModpackReport:
    instance: str
    info: ModpackInfo
    downloads: DownloadReport
    loader_installed: bool = False

    @property
    def ok(self) -> bool:
        return not self.downloads.failures


def _safe_target(root: Path, relative: str) -> Path | None:
    """把 zip 里的相对路径落到实例目录里，挡掉 ../ 之类越界。"""
    relative = relative.replace("\\", "/").lstrip("/")
    if not relative or relative.endswith("/"):
        return None
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _install_loader(
    layout: GameLayout,
    instance: str,
    info: ModpackInfo,
    *,
    fetcher: Fetcher,
    progress,
) -> bool:
    if not info.loader:
        return False
    progress("loader", f"安装 {info.loader} {info.loader_version or ''}".strip())
    try:
        if info.loader == "fabric":
            install_loader.attach_fabric(
                layout, instance, loader_version=info.loader_version, fetcher=fetcher
            )
        elif info.loader == "forge":
            install_loader.install_forge(
                layout, instance, forge_version=info.loader_version, fetcher=fetcher
            )
        elif info.loader == "neoforge":
            install_loader.install_neoforge(
                layout, instance, neoforge_version=info.loader_version, fetcher=fetcher
            )
    except install_loader.LoaderInstallError as exc:
        raise ModpackError(f"装加载器失败：{exc}") from exc
    return True


def install_modpack(
    layout: GameLayout,
    archive_path: Path,
    *,
    instance_name: str | None = None,
    info: ModpackInfo | None = None,
    game_version: str | None = None,
    fetcher: Fetcher,
    progress=None,
) -> ModpackReport:
    def notify(stage: str, message: str) -> None:
        if progress is not None:
            progress(stage, message)

    info = info or detect(archive_path)
    version = (game_version or info.game_version or "").strip()
    if not version:
        raise ModpackError("识别不出游戏版本，请手动指定（例如 1.20.1）")

    instance = install_vanilla.validate_instance_name(instance_name or info.name or version)
    if layout.instance_exists(instance) or layout.version_dir(instance).exists():
        raise install_vanilla.InstanceExistsError(f"实例「{instance}」已经存在")

    notify("vanilla", f"安装原版 {version}…")
    report = install_vanilla.install_version(
        layout, version, instance_name=instance, fetcher=fetcher
    )
    if not report.ok:
        raise ModpackError("原版安装失败：" + str(report.downloads.failures[:3]))

    loader_installed = _install_loader(layout, instance, info, fetcher=fetcher, progress=notify)

    root = layout.version_dir(instance)
    jobs: list[DownloadJob] = []
    for entry in info.files:
        target = _safe_target(root, entry.path)
        if target is None or not entry.urls:
            continue
        jobs.append(
            DownloadJob(
                urls=entry.urls,
                destination=target,
                sha1=entry.sha1,
                size=entry.size,
                label=entry.path,
            )
        )
    downloads = DownloadReport()
    if jobs:
        notify("files", f"下载 {len(jobs)} 个整合包文件…")
        downloads.merge(fetcher.download_all(jobs))

    notify("overrides", "解压覆盖文件…")
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            matched = next((prefix for prefix in info.overrides if name.startswith(prefix)), None)
            if matched is None or name in info.skip:
                continue
            relative = name[len(matched) :]
            target = _safe_target(root, relative)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("wb") as sink:
                sink.write(source.read())

    metadata = {
        "provider": info.provider,
        "name": info.name,
        "version": info.version,
        "gameVersion": version,
        "loader": info.loader,
        "loaderVersion": info.loader_version,
        "notes": info.notes,
        "files": [entry.path for entry in info.files],
    }
    (root / "gelauncher-modpack.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    notify("done", f"{instance} 导入完成")
    return ModpackReport(
        instance=instance,
        info=info,
        downloads=downloads,
        loader_installed=loader_installed,
    )
