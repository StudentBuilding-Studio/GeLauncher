"""装加载器：Fabric 走 meta profile JSON，Forge 走官方 installer。

这里和方案原文有一处重要偏离（2026-10-31 实测）：
- 方案原本打算"Fabric/Forge 都调官方 installer"；
- 但实测 ``maven.fabricmc.net`` 从本机 **TLS 握手每次都失败**，而 BMCLAPI 不镜像
  ``fabric-installer`` 这个 jar（404），所以 Fabric 的 installer 根本下不下来；
- 而 BMCLAPI 提供了完整的 ``/fabric-meta/.../profile/json`` 和 fabric 的 Maven 镜像，
  这正是 HMCL 的做法：拿 profile JSON 自己写 ``versions/<id>/<id>.json`` + 下 8 个库。
  Fabric 的 profile JSON 就是"标准版本 JSON + inheritsFrom"，和 installer 产物等价。

Forge 仍然调官方 installer：installer 本身能从 BMCLAPI 下到（6.3MB/1.4s），
并且它支持 ``--mirror``，可以把库下载指到镜像。
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import libraries
from .java import JavaRuntime, find_java
from .layout import GameLayout
from .models import VersionJson, VersionJsonError
from .net import DEFAULT_SOURCES, DownloadJob, DownloadReport, Fetcher, Source
from .rules import Environment, allow

FABRIC_META = "https://meta.fabricmc.net/v2"
FABRIC_MAVEN = "https://maven.fabricmc.net"
FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
BMCLAPI_MAVEN = "https://bmclapi2.bangbang93.com/maven/"

INSTALLER_TIMEOUT = 15 * 60

ProgressHook = Callable[[str, str], None]


class LoaderInstallError(RuntimeError):
    """加载器安装失败。message 里会带上 installer 输出的最后几十行。"""


@dataclass
class LoaderReport:
    version_id: str
    loader: str
    installer_path: Path | None = None
    installer_output: list[str] = field(default_factory=list)
    downloads: DownloadReport = field(default_factory=DownloadReport)


def _notify(hook: ProgressHook | None, stage: str, message: str) -> None:
    if hook is not None:
        hook(stage, message)


def _ensure_cache(fetcher: Fetcher, layout: GameLayout) -> Fetcher:
    """元数据走磁盘缓存（HMCL 的 cache repository 思路），重复安装基本不花时间。"""
    if fetcher.cache_dir is not None:
        return fetcher
    return replace(fetcher, cache_dir=layout.root / "launcher" / "cache" / "meta")


def _prefer_mirror(fetcher: Fetcher) -> Fetcher:
    """加载器相关的下载多给几次重试。

    不在这里硬把镜像排前面：Fetcher 自己会做 5 秒可达性探测，
    通不过的"源×主机"会被拉黑，然后自动落到镜像上。
    这样既能快，又不会在镜像比官方慢的时候（实测 assets 上慢 30 倍）吃亏。
    """
    return replace(fetcher, retries=max(fetcher.retries, 3))


# --------------------------------------------------------------------------- #
# 版本查询
# --------------------------------------------------------------------------- #


def fabric_loader_versions(fetcher: Fetcher, mc_version: str) -> list[str]:
    """某个 MC 版本可用的 fabric-loader 版本，最新在前。"""
    url = f"{FABRIC_META}/versions/loader/{mc_version}"
    raw = fetcher.get_bytes(url, label=f"fabric loader 列表（{mc_version}）", ttl=600)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderInstallError(f"Fabric meta 返回的不是 JSON：{exc}") from exc
    stable = []
    everything = []
    for entry in data:
        loader = entry.get("loader") or {}
        version = loader.get("version")
        if not version:
            continue
        everything.append(version)
        if loader.get("stable", False):
            stable.append(version)
    versions = stable or everything
    if not versions:
        raise LoaderInstallError(
            f"Fabric 没有支持 {mc_version} 的 loader（这个版本可能太老或太新）"
        )
    return versions


def latest_fabric_loader(fetcher: Fetcher, mc_version: str) -> str:
    return fabric_loader_versions(fetcher, mc_version)[0]


def latest_fabric_installer(fetcher: Fetcher) -> tuple[str, str]:
    """返回 (installer 版本, 下载地址)。只在显式要求用 installer 时才需要。"""
    raw = fetcher.get_bytes(f"{FABRIC_META}/versions/installer", label="fabric installer 列表", ttl=600)
    data = json.loads(raw.decode("utf-8"))
    if not data:
        raise LoaderInstallError("Fabric meta 没有返回任何 installer 版本")
    entry = data[0]
    version = entry["version"]
    url = entry.get("url") or (
        f"{FABRIC_MAVEN}/net/fabricmc/fabric-installer/{version}/"
        f"fabric-installer-{version}.jar"
    )
    return version, url


def forge_promotions(fetcher: Fetcher) -> dict[str, str]:
    raw = fetcher.get_bytes(FORGE_PROMOTIONS, label="Forge promotions", ttl=600)
    return json.loads(raw.decode("utf-8")).get("promos", {})


def _version_sort_key(text: str) -> list:
    # 数字段比字母段"大"，避免 int 和 str 直接比较抛 TypeError
    return [
        (1, int(part)) if part.isdigit() else (0, part.lower())
        for part in text.replace("-", ".").split(".")
        if part
    ]


def forge_versions_from_mirror(fetcher: Fetcher, mc_version: str) -> list[str]:
    """BMCLAPI 的 ``/forge/minecraft/<mc>`` 接口（实测 200，比 files.minecraftforge.net 可靠）。"""
    raw = fetcher.get_bytes(
        f"https://bmclapi2.bangbang93.com/forge/minecraft/{mc_version}",
        label=f"Forge 版本列表（{mc_version}）", ttl=600,
    )
    data = json.loads(raw.decode("utf-8"))
    versions = [str(entry.get("version")) for entry in data if entry.get("version")]
    return versions


def forge_versions_from_maven(fetcher: Fetcher, mc_version: str) -> list[str]:
    """最后兜底：从 maven-metadata.xml 里筛 ``<mc>-<ver>``。"""
    raw = fetcher.get_bytes(
        f"{FORGE_MAVEN}/net/minecraftforge/forge/maven-metadata.xml",
        label="Forge maven-metadata", ttl=600,
    )
    text = raw.decode("utf-8", "replace")
    import re

    prefix = mc_version + "-"
    versions = []
    for match in re.finditer(r"<version>([^<]+)</version>", text):
        value = match.group(1)
        if value.startswith(prefix):
            versions.append(value[len(prefix) :])
    return versions


def forge_version_for(fetcher: Fetcher, mc_version: str, prefer: str = "recommended") -> str:
    """挑一个 Forge 版本。

    顺序很重要（都是实测出来的）：
    1. BMCLAPI 的版本列表——最快最稳；
    2. 官方 promotions_slim.json——最"权威"，但 files.minecraftforge.net 经常超时；
    3. maven-metadata.xml——最后兜底。
    """
    errors: list[str] = []
    for loader in (
        lambda: forge_versions_from_mirror(fetcher, mc_version),
        lambda: _promo_versions(fetcher, mc_version, prefer),
        lambda: forge_versions_from_maven(fetcher, mc_version),
    ):
        try:
            versions = loader()
        except Exception as exc:  # noqa: BLE001 - 每个源都可能挂，记录下来换下一个
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        clean = [v for v in versions if "beta" not in v.lower() and "pre" not in v.lower()]
        candidates = clean or versions
        if candidates:
            return max(candidates, key=_version_sort_key)
    raise LoaderInstallError(
        f"拿不到 {mc_version} 的 Forge 版本（{'; '.join(errors)}）\n"
        "可以手动指定：--forge-version 1.21.8-58.0.0"
    )


def _promo_versions(fetcher: Fetcher, mc_version: str, prefer: str) -> list[str]:
    promos = forge_promotions(fetcher)
    order = ["recommended", "latest"] if prefer == "recommended" else ["latest", "recommended"]
    result = []
    for kind in order:
        value = promos.get(f"{mc_version}-{kind}")
        if value:
            result.append(value)
    result.extend(v for k, v in promos.items() if k.startswith(f"{mc_version}-"))
    return result


def fabric_version_id(mc_version: str, loader_version: str) -> str:
    return f"fabric-loader-{loader_version}-{mc_version}"


def forge_version_id(mc_version: str, forge_version: str) -> str:
    forge = (
        forge_version.split("-", 1)[1]
        if forge_version.startswith(mc_version + "-")
        else forge_version
    )
    return f"{mc_version}-forge-{forge}"


# --------------------------------------------------------------------------- #
# Fabric：profile JSON 方式
# --------------------------------------------------------------------------- #


def fetch_fabric_profile(fetcher: Fetcher, mc_version: str, loader_version: str) -> dict:
    url = f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json"
    raw = fetcher.get_bytes(url, label=f"fabric profile（{mc_version}/{loader_version}）", ttl=3600)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderInstallError(f"Fabric profile 不是合法 JSON：{exc}") from exc


def install_fabric(
    layout: GameLayout,
    mc_version: str,
    *,
    loader_version: str | None = None,
    fetcher: Fetcher | None = None,
    java: JavaRuntime | None = None,
    progress: ProgressHook | None = None,
    env: Environment | None = None,
    use_installer: bool = False,
) -> LoaderReport:
    """装 Fabric。

    默认走 profile JSON（不需要 Java、不依赖那台上不去的 maven）；
    ``use_installer=True`` 时才真的去跑 fabric-installer。
    """
    fetcher = _prefer_mirror(fetcher or Fetcher(sources=DEFAULT_SOURCES))
    fetcher = _ensure_cache(fetcher, layout)
    require_vanilla(layout, mc_version)
    env = env or Environment()

    _notify(progress, "resolve", f"查 Fabric loader 版本（MC {mc_version}）…")
    loader_version = loader_version or latest_fabric_loader(fetcher, mc_version)

    if use_installer:
        return _install_fabric_via_installer(
            layout,
            mc_version,
            loader_version,
            fetcher=fetcher,
            java=java,
            progress=progress,
        )

    _notify(progress, "profile", f"取 Fabric profile（loader {loader_version}）…")
    profile = fetch_fabric_profile(fetcher, mc_version, loader_version)
    version_id = str(profile.get("id") or fabric_version_id(mc_version, loader_version))
    profile["id"] = version_id
    profile.setdefault("inheritsFrom", mc_version)

    try:
        version = VersionJson.from_dict(profile, f"fabric-profile({version_id})")
    except VersionJsonError as exc:
        raise LoaderInstallError(f"Fabric profile 解析失败：{exc}") from exc

    version_dir = layout.version_dir(version_id)
    version_dir.mkdir(parents=True, exist_ok=True)
    json_path = layout.version_json_path(version_id)
    json_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    jobs = _library_jobs(layout, version, env)
    _notify(progress, "libraries", f"下载 {len(jobs)} 个 Fabric 库…")
    done = 0

    def on_done(job: DownloadJob, _report: DownloadReport) -> None:
        nonlocal done
        done += 1
        _notify(progress, "libraries", f"{done}/{len(jobs)} {job.destination.name}")

    report = fetcher.download_all(jobs, on_done=on_done)
    if not report.ok:
        raise LoaderInstallError(
            "Fabric 库下载失败：\n"
            + "\n".join(f"  - {name}：{reason}" for name, reason in report.failures[:10])
        )

    verify_installed(layout, version_id, mc_version)
    _notify(progress, "done", f"装好了：{version_id}")
    return LoaderReport(version_id=version_id, loader="fabric", downloads=report)


# --------------------------------------------------------------------------- #
# Fabric：装进现有版本（和 HMCL 一样，不新建版本目录）
# --------------------------------------------------------------------------- #
# 把加载器装进现有版本（Fabric / Forge / NeoForge 共用）
#
# 做法和 HMCL 的 patch 一样：不改版本名，直接把加载器的 mainClass / libraries /
# arguments 合并进 versions/<id>/<id>.json，原文件先备份，随时可以还原。
# --------------------------------------------------------------------------- #

BACKUP_SUFFIX = ".vanilla-backup"
FABRIC_BACKUP_SUFFIX = BACKUP_SUFFIX  # 兼容旧名字

BMCLAPI_ROOT = "https://bmclapi2.bangbang93.com"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"

LOADER_LABELS = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge"}


def loader_of(layout: GameLayout, version_id: str) -> str | None:
    """这个版本现在装的是哪个加载器（没有就 None）。"""
    try:
        version = layout.load_json(version_id)
    except VersionJsonError:
        return None
    main = (version.main_class or "").lower()
    names = " ".join(lib.name.lower() for lib in version.libraries)
    if "knot" in main or "fabric" in main:
        return "fabric"
    if "net.neoforged" in names or "neoforge" in main:
        return "neoforge"
    if "net.minecraftforge" in names or "forge" in main:
        return "forge"
    return None


def fabric_attached(layout: GameLayout, version_id: str) -> bool:
    return loader_of(layout, version_id) == "fabric"


def _require_no_other_loader(layout: GameLayout, mc_version: str, loader: str) -> None:
    current = loader_of(layout, mc_version)
    if current and current != loader:
        raise LoaderInstallError(
            f"{mc_version} 现在装的是 {LOADER_LABELS.get(current, current)}，"
            f"先卸载它再装 {LOADER_LABELS.get(loader, loader)}。"
        )


def _maven_key(name: str) -> str:
    """group:artifact[:classifier]，用来做合并去重（不含版本号）。"""
    body = name.split("@", 1)[0]
    parts = body.split(":")
    if len(parts) >= 4:
        return f"{parts[0]}:{parts[1]}:{parts[3]}"
    return f"{body.split(':')[0]}:{body.split(':')[1]}" if ":" in body else body


def _fabric_library_entry(entry: dict) -> dict:
    """给只有 name+url 的库（Fabric meta）补上 downloads.artifact。"""
    from .libraries import LibraryError, MavenCoordinate

    name = str(entry.get("name") or "")
    result = dict(entry)
    try:
        coordinate = MavenCoordinate.parse(name)
    except LibraryError:
        return result
    base = str(entry.get("url") or FABRIC_MAVEN).rstrip("/") + "/"
    path = coordinate.path
    result.setdefault("downloads", {})
    result["downloads"] = {"artifact": {"path": path, "url": base + path}}
    return result


def _merge_loader_json(
    layout: GameLayout, mc_version: str, patch: dict, *, loader: str
) -> dict:
    """把加载器的版本 JSON 合并进原版 JSON（原文件备份一次）。"""
    json_path = layout.version_json_path(mc_version)
    original_text = json_path.read_text(encoding="utf-8-sig")
    raw = json.loads(original_text)
    backup = json_path.with_name(json_path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        # 只在第一次装的时候备份，重复安装不会把"已装加载器"的状态备份进去
        backup.write_text(original_text, encoding="utf-8")

    merged: list[dict] = [dict(item) for item in (raw.get("libraries") or [])]
    index = {_maven_key(str(item.get("name", ""))): position for position, item in enumerate(merged)}
    for entry in patch.get("libraries") or []:
        item = dict(entry)
        if not item.get("downloads"):
            item = _fabric_library_entry(item)
        key = _maven_key(str(item.get("name", "")))
        if key in index:
            merged[index[key]] = item
        else:
            index[key] = len(merged)
            merged.append(item)
    raw["libraries"] = merged

    if patch.get("mainClass"):
        raw["mainClass"] = patch["mainClass"]
    if patch.get("jar"):
        raw["jar"] = patch["jar"]

    base_args = raw.get("arguments") or {}
    patch_args = patch.get("arguments") or {}
    merged_args = {}
    for section in ("jvm", "game"):
        values = list(base_args.get(section) or [])
        # 这里不能去重：JVM 参数是有配对关系的（--add-opens <值>、-cp <值>），
        # 去掉一个同名 token 会让后面的值变成"主类"，实测 NeoForge 26.3 就是这么崩的。
        values.extend(patch_args.get(section) or [])
        if values:
            merged_args[section] = values
    if merged_args:
        raw["arguments"] = merged_args

    raw.pop("inheritsFrom", None)
    raw.setdefault("id", mc_version)
    raw["_geLoader"] = {"type": loader}
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


def attach_fabric(
    layout: GameLayout,
    mc_version: str,
    *,
    loader_version: str | None = None,
    fetcher: Fetcher | None = None,
    progress: ProgressHook | None = None,
    env: Environment | None = None,
) -> LoaderReport:
    """把 Fabric 合并进现有版本（版本 id 不变，启动它即启动 Fabric）。"""
    fetcher = _ensure_cache(_prefer_mirror(fetcher or Fetcher(sources=DEFAULT_SOURCES)), layout)
    require_vanilla(layout, mc_version)
    _require_no_other_loader(layout, mc_version, "fabric")
    env = env or Environment()

    _notify(progress, "resolve", f"查 Fabric loader 版本（MC {mc_version}）…")
    loader_version = loader_version or latest_fabric_loader(fetcher, mc_version)

    _notify(progress, "profile", f"取 Fabric profile（loader {loader_version}）…")
    profile = fetch_fabric_profile(fetcher, mc_version, loader_version)

    raw = _merge_loader_json(layout, mc_version, profile, loader="fabric")

    # ---- 下载 Fabric 的库 ----
    merged_version = VersionJson.from_dict(raw, f"merged({mc_version})")
    jobs = _library_jobs(layout, merged_version, env)
    _notify(progress, "libraries", f"下载 {len(jobs)} 个库…")
    done = 0

    def on_done(job: DownloadJob, _report: DownloadReport) -> None:
        nonlocal done
        done += 1
        _notify(progress, "libraries", f"{done}/{len(jobs)} {job.destination.name}")

    report = fetcher.download_all(jobs, on_done=on_done)
    if not report.ok:
        raise LoaderInstallError(
            "库下载失败：\n" + "\n".join(f"  - {n}：{r}" for n, r in report.failures[:10])
        )

    if not fabric_attached(layout, mc_version):
        raise LoaderInstallError("写入后 mainClass 里没有 fabric，合并好像失败了")
    _notify(progress, "done", f"{mc_version} 已装上 Fabric {loader_version}")
    return LoaderReport(version_id=mc_version, loader="fabric", downloads=report)


def detach_loader(layout: GameLayout, mc_version: str) -> bool:
    """卸载加载器：把版本 JSON 还原成备份。"""
    json_path = layout.version_json_path(mc_version)
    backup = json_path.with_name(json_path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        return False
    json_path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    backup.unlink(missing_ok=True)
    return True


def detach_fabric(layout: GameLayout, mc_version: str) -> bool:
    return detach_loader(layout, mc_version)


# --------------------------------------------------------------------------- #
# NeoForge：版本列表走 BMCLAPI 的专用接口（和 HMCL 一样）
# --------------------------------------------------------------------------- #


def _neoforge_mc_version(version: str) -> str | None:
    """从 NeoForge 版本号反推对应的 MC 版本（移植 HMCL 的解析规则）。"""
    try:
        first = version.index(".")
        second = version.index(".", first + 1)
        major = int(version[:first])
        if major == 0:  # 快照版
            return version[first + 1 : second]
        if major >= 26:
            third = version.find(".", second + 1)
            if third < 0:
                return None
            ver = version[:second] if int(version[second + 1 : third]) == 0 else version[:third]
            plus = version.find("+")
            return ver if plus < 0 else f"{ver}-{version[plus + 1:]}"
        ver = version[:first] if int(version[first + 1 : second]) == 0 else version[:second]
        return "1." + ver
    except ValueError:
        return None


def neoforge_versions(fetcher: Fetcher, mc_version: str) -> list[str]:
    """列 NeoForge 版本：先 BMCLAPI 专用接口，失败再用官方 maven API。"""
    from .net import DownloadError

    sources = [
        (f"{BMCLAPI_ROOT}/neoforge/list/{mc_version}", "bmcl"),
        (
            f"{NEOFORGE_MAVEN}/api/maven/versions/releases/net/neoforged/neoforge",
            "official",
        ),
    ]
    last_error: Exception | None = None
    for url, kind in sources:
        try:
            raw = fetcher.get_bytes(url, label="NeoForge 版本列表", ttl=600)
        except DownloadError as exc:
            last_error = exc
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items: list[str] = []
        if kind == "bmcl" and isinstance(data, list):
            items = [
                str(item["version"])
                for item in data
                if isinstance(item, dict) and item.get("version")
            ]
        elif kind == "official" and isinstance(data, dict):
            items = [
                str(version)
                for version in (data.get("versions") or [])
                if isinstance(version, str) and _neoforge_mc_version(version) == mc_version
            ]
        stable = [v for v in items if "beta" not in v and "alpha" not in v]
        if stable:
            return sorted(set(stable), key=_version_sort_key)
    raise LoaderInstallError(f"查不到 NeoForge 版本（MC {mc_version}）：{last_error}")


def latest_neoforge(fetcher: Fetcher, mc_version: str) -> str:
    versions = neoforge_versions(fetcher, mc_version)
    if not versions:
        raise LoaderInstallError(f"MC {mc_version} 没有可用的 NeoForge")
    return versions[-1]


def _new_version_dir(layout: GameLayout, before: set[str]) -> str:
    after = set(layout.installed_versions())
    new = after - before
    if len(new) == 1:
        return new.pop()
    if not new:
        raise LoaderInstallError("installer 跑完了，但没生成新的版本目录")
    raise LoaderInstallError(f"installer 生成了多个版本目录：{', '.join(sorted(new))}")


def _absorb_generated_version(
    layout: GameLayout, mc_version: str, generated: str, *, loader: str
) -> None:
    """把 installer 生成的那个版本合并进原版版本，然后删掉生成目录。"""
    import shutil

    generated_dir = layout.version_dir(generated)
    json_path = layout.version_json_path(generated)
    if not json_path.is_file():
        raise LoaderInstallError(f"installer 没写出 {generated}.json")
    patch = json.loads(json_path.read_text(encoding="utf-8-sig"))

    # 老版本（如 1.12.2）会生成一个独立的可执行 jar，搬到原版版本目录里
    if patch.get("jar"):
        source = generated_dir / f"{patch['jar']}.jar"
        if source.is_file():
            target_name = f"{mc_version}-{loader}"
            shutil.copy2(source, layout.version_dir(mc_version) / f"{target_name}.jar")
            patch["jar"] = target_name

    _merge_loader_json(layout, mc_version, patch, loader=loader)
    if generated != mc_version:
        shutil.rmtree(generated_dir, ignore_errors=True)


def _library_jobs(
    layout: GameLayout, version: VersionJson, env: Environment
) -> list[DownloadJob]:
    jobs: list[DownloadJob] = []
    for library in version.libraries:
        if not allow(library.rules, env):
            continue
        artifact = library.artifact or libraries.synthetic_artifact(library)
        if artifact is None:
            continue
        relative = libraries.artifact_relative_path(library, artifact)
        urls = [artifact.url] if artifact.url else []
        if not urls:
            base = library.url or FABRIC_MAVEN
            urls = [f"{base.rstrip('/')}/{relative}"]
        jobs.append(
            DownloadJob(
                urls=urls,
                destination=layout.libraries_dir / relative,
                sha1=artifact.sha1,
                size=artifact.size,
                label=relative,
            )
        )
    return jobs


# --------------------------------------------------------------------------- #
# Fabric：installer 方式（备用，需要能访问 maven.fabricmc.net）
# --------------------------------------------------------------------------- #


def _install_fabric_via_installer(
    layout: GameLayout,
    mc_version: str,
    loader_version: str,
    *,
    fetcher: Fetcher,
    java: JavaRuntime | None,
    progress: ProgressHook | None,
) -> LoaderReport:
    installer_version, installer_url = latest_fabric_installer(fetcher)
    cache_dir = layout.root / "launcher" / "installers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    installer_path = cache_dir / f"fabric-installer-{installer_version}.jar"
    _notify(progress, "download", f"下载 fabric-installer {installer_version}…")
    _download_installer(fetcher, [installer_url], installer_path, installer_path.name)

    java = java or find_java(None)
    _notify(progress, "install", f"运行 Fabric installer（Java {java.major}）…")
    output = run_installer(
        java,
        installer_path,
        [
            "client",
            "-dir",
            str(layout.root),
            "-mcversion",
            mc_version,
            "-loader",
            loader_version,
            "-noprofile",
        ],
        cwd=layout.root,
        on_line=lambda line: _notify(progress, "log", line),
    )
    version_id = fabric_version_id(mc_version, loader_version)
    verify_installed(layout, version_id, mc_version)
    return LoaderReport(
        version_id=version_id,
        loader="fabric",
        installer_path=installer_path,
        installer_output=output,
    )


# --------------------------------------------------------------------------- #
# Forge
# --------------------------------------------------------------------------- #


def seed_install_profile_libraries(
    fetcher: Fetcher,
    layout: GameLayout,
    installer_path: Path,
    *,
    progress: ProgressHook | None = None,
) -> tuple[int, list[tuple[str, str]]]:
    """把 installer 的 ``install_profile.json`` 里列的库先下好。

    为什么需要这一步（实测）：Forge installer 自己的 HTTP 客户端在跟随
    BMCLAPI 的 302 跳到 cernet 镜像时会被 403 挡掉（同一个 URL 用 Python 下载却是 200），
    于是安装会卡在 ``mcp_config@zip`` 上失败。先把这些文件按 sha1 下到
    ``libraries/``，installer 会"File exists: Checksum validated"直接跳过。
    """
    import zipfile

    try:
        with zipfile.ZipFile(installer_path) as archive:
            raw = archive.read("install_profile.json")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise LoaderInstallError(f"读不了 installer 的 install_profile.json：{exc}") from exc
    profile = json.loads(raw.decode("utf-8"))
    entries = profile.get("libraries") or []
    jobs: list[DownloadJob] = []
    for entry in entries:
        artifact = (entry.get("downloads") or {}).get("artifact") or {}
        path = artifact.get("path")
        url = artifact.get("url")
        if not path or not url:
            continue
        jobs.append(
            DownloadJob(
                urls=[url],
                destination=layout.libraries_dir / path,
                sha1=artifact.get("sha1"),
                size=artifact.get("size"),
                label=path,
            )
        )
    if not jobs:
        return 0, []
    _notify(progress, "seed", f"预下载 installer 需要的 {len(jobs)} 个库…")
    report = fetcher.download_all(jobs)
    return report.downloaded + report.skipped, report.failures


def install_forge(
    layout: GameLayout,
    mc_version: str,
    *,
    forge_version: str | None = None,
    fetcher: Fetcher | None = None,
    java: JavaRuntime | None = None,
    progress: ProgressHook | None = None,
    use_mirror: bool = True,
    attach: bool = True,
) -> LoaderReport:
    fetcher = _prefer_mirror(fetcher or Fetcher(sources=DEFAULT_SOURCES))
    fetcher = _ensure_cache(fetcher, layout)
    require_vanilla(layout, mc_version)
    if attach:
        _require_no_other_loader(layout, mc_version, "forge")

    _notify(progress, "resolve", f"查 Forge 版本（MC {mc_version}）…")
    forge_version = forge_version or forge_version_for(fetcher, mc_version)
    coordinate = (
        forge_version
        if forge_version.startswith(mc_version + "-")
        else f"{mc_version}-{forge_version}"
    )

    cache_dir = layout.root / "launcher" / "installers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    installer_name = f"forge-{coordinate}-installer.jar"
    installer_path = cache_dir / installer_name
    _notify(progress, "download", f"下载 {installer_name}…")
    _download_installer(
        fetcher,
        [f"{FORGE_MAVEN}/net/minecraftforge/forge/{coordinate}/{installer_name}"],
        installer_path,
        installer_name,
    )

    seeded, failures = seed_install_profile_libraries(
        fetcher, layout, installer_path, progress=progress
    )
    if failures:
        _notify(
            progress,
            "seed",
            f"[警告] {len(failures)} 个库预下载失败，交给 installer 自己重试："
            + ", ".join(name for name, _ in failures[:3]),
        )
    else:
        _notify(progress, "seed", f"{seeded} 个库已就位")

    java = java or find_java(None)
    args = ["--installClient", str(layout.root)]
    if use_mirror:
        # Forge installer 的 --mirror 会把库下载指到镜像，避免直连 maven.minecraftforge.net
        args += ["--mirror", BMCLAPI_MAVEN]
    _notify(progress, "install", f"运行 Forge installer（Java {java.major}）…")
    output = run_installer(
        java,
        installer_path,
        args,
        cwd=layout.root,
        on_line=lambda line: _notify(progress, "log", line),
    )

    generated = forge_version_id(mc_version, coordinate)
    verify_installed(layout, generated, mc_version)
    if not attach:
        return LoaderReport(
            version_id=generated,
            loader="forge",
            installer_path=installer_path,
            installer_output=output,
        )

    _absorb_generated_version(layout, mc_version, generated, loader="forge")
    if loader_of(layout, mc_version) != "forge":
        raise LoaderInstallError("Forge 合并进原版版本失败")
    _notify(progress, "done", f"{mc_version} 已装上 Forge（{coordinate}）")
    return LoaderReport(
        version_id=mc_version,
        loader="forge",
        installer_path=installer_path,
        installer_output=output,
    )


def install_neoforge(
    layout: GameLayout,
    mc_version: str,
    *,
    neoforge_version: str | None = None,
    fetcher: Fetcher | None = None,
    java: JavaRuntime | None = None,
    progress: ProgressHook | None = None,
    use_mirror: bool = True,
    attach: bool = True,
) -> LoaderReport:
    fetcher = _prefer_mirror(fetcher or Fetcher(sources=DEFAULT_SOURCES))
    fetcher = _ensure_cache(fetcher, layout)
    require_vanilla(layout, mc_version)
    if attach:
        _require_no_other_loader(layout, mc_version, "neoforge")

    _notify(progress, "resolve", f"查 NeoForge 版本（MC {mc_version}）…")
    version = neoforge_version or latest_neoforge(fetcher, mc_version)

    cache_dir = layout.root / "launcher" / "installers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    installer_name = f"neoforge-{version}-installer.jar"
    installer_path = cache_dir / installer_name
    _notify(progress, "download", f"下载 {installer_name}…")
    _download_installer(
        fetcher,
        [
            f"{BMCLAPI_ROOT}/neoforge/version/{version}/download/installer.jar",
            f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/{version}/{installer_name}",
        ],
        installer_path,
        installer_name,
    )

    java = java or find_java(None)
    # NeoForge installer 不认 --mirror，会直接连 maven.neoforged.net（实测超时），
    # 所以先把 install_profile 里的库用我们自己的下载器（带镜像/赛跑）备好。
    seeded, failures = seed_install_profile_libraries(
        fetcher, layout, installer_path, progress=progress
    )
    if failures:
        _notify(
            progress,
            "seed",
            f"[警告] {len(failures)} 个库预下载失败："
            + ", ".join(name for name, _ in failures[:3]),
        )
    else:
        _notify(progress, "seed", f"{seeded} 个库已就位")

    args = ["--installClient", str(layout.root)]
    if use_mirror:
        args += ["--mirror", BMCLAPI_MAVEN]
    before = set(layout.installed_versions())
    _notify(progress, "install", f"运行 NeoForge installer（Java {java.major}）…")
    output = run_installer(
        java,
        installer_path,
        args,
        cwd=layout.root,
        on_line=lambda line: _notify(progress, "log", line),
    )

    generated = _new_version_dir(layout, before)
    verify_installed(layout, generated, mc_version)
    if not attach:
        return LoaderReport(
            version_id=generated,
            loader="neoforge",
            installer_path=installer_path,
            installer_output=output,
        )

    _absorb_generated_version(layout, mc_version, generated, loader="neoforge")
    if loader_of(layout, mc_version) != "neoforge":
        raise LoaderInstallError("NeoForge 合并进原版版本失败")
    _notify(progress, "done", f"{mc_version} 已装上 NeoForge（{version}）")
    return LoaderReport(
        version_id=mc_version,
        loader="neoforge",
        installer_path=installer_path,
        installer_output=output,
    )


# --------------------------------------------------------------------------- #
# 公共
# --------------------------------------------------------------------------- #


def _download_installer(fetcher: Fetcher, urls: list[str], destination: Path, label: str) -> Path:
    from .net import DownloadError

    try:
        report = fetcher.download(DownloadJob(urls=urls, destination=destination, label=label))
    except DownloadError as exc:
        raise LoaderInstallError(f"installer 下载失败：{exc}") from exc
    if not report.ok:
        raise LoaderInstallError(f"installer 下载失败：{report.failures}")
    return destination


def run_installer(
    java: JavaRuntime,
    installer: Path,
    args: list[str],
    *,
    cwd: Path,
    timeout: int = INSTALLER_TIMEOUT,
    on_line: Callable[[str], None] | None = None,
) -> list[str]:
    """跑 installer，把输出逐行喂给回调，失败时抛出带尾部日志的异常。"""
    command = [str(java.path), "-jar", str(installer), *args]
    output: list[str] = []
    try:
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
    except OSError as exc:
        raise LoaderInstallError(f"启动 installer 失败：{exc}") from exc

    assert process.stdout is not None

    def pump() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            text = line.rstrip("\r\n")
            output.append(text)
            if on_line is not None:
                on_line(text)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise LoaderInstallError(
            f"installer 超过 {timeout // 60} 分钟没结束，已终止。\n最后输出：\n{tail(output)}"
        ) from None
    thread.join(timeout=5)

    if returncode != 0:
        raise LoaderInstallError(
            f"installer 退出码 {returncode}，安装失败。\n最后 50 行输出：\n{tail(output)}"
        )
    return output


def tail(lines: list[str], count: int = 50) -> str:
    return "\n".join(lines[-count:])


def require_vanilla(layout: GameLayout, mc_version: str) -> None:
    if not layout.version_json_path(mc_version).is_file():
        raise LoaderInstallError(
            f"还没装原版 {mc_version}：找不到 {layout.version_json_path(mc_version)}\n"
            f"先运行：ge install --version-id {mc_version}"
        )


def verify_installed(layout: GameLayout, version_id: str, mc_version: str) -> None:
    """装完后的机制验收：目录在、JSON 在、inheritsFrom 指回原版。"""
    json_path = layout.version_json_path(version_id)
    if not json_path.is_file():
        installed = ", ".join(layout.installed_versions()) or "（空）"
        raise LoaderInstallError(
            f"没找到 {json_path}\n现在的版本目录：{installed}\n"
            "可能 installer 把文件装到别处了，或者版本命名规则变了。"
        )
    data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    parent = data.get("inheritsFrom")
    if parent and parent != mc_version:
        raise LoaderInstallError(
            f"{version_id} 的 inheritsFrom 是 {parent!r}，期望 {mc_version!r}。"
            "安装可能装到了别的原版上。"
        )
