"""安装原版：version_manifest → 版本 JSON → 库 / 资源 / 客户端 jar。

数据流（方案 §4.2）：
    GET version_manifest_v2.json → 选版本 → GET 版本 JSON（落盘）
      → 库：downloads.artifact / 坐标 + sha1
      → 资源：assetIndex → objects/<hash[:2]>/<hash>
      → 客户端 jar：downloads.client
      → 日志配置：logging.client
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

from . import libraries
from .layout import GameLayout
from .models import VersionJson, VersionJsonError
from .net import DEFAULT_SOURCES, DownloadJob, DownloadReport, Fetcher, Source, for_bulk_assets
from .rules import Environment, allow

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
LIBRARIES_BASE = "https://libraries.minecraft.net/"
ASSETS_BASE = "https://resources.download.minecraft.net"

#: installer（Fabric/Forge）要求这个文件存在，内容是空壳即可
EMPTY_PROFILES = {"profiles": {}, "settings": {}, "version": 3}

ProgressHook = Callable[[str, int, int, str], None]


class InstallError(RuntimeError):
    """安装过程中不可恢复的错误。"""


class InstanceExistsError(InstallError):
    """同名实例已经存在。"""


#: Windows 下不能出现在文件夹名里的字符
_INSTANCE_FORBIDDEN = set('<>:"/\\|?*')


def validate_instance_name(name: str) -> str:
    """实例名就是文件夹名：不能为空、不能带路径分隔符、不能有 Windows 保留字符。"""
    text = (name or "").strip()
    if not text:
        raise InstallError("实例名不能为空")
    if text in (".", "..") or set(text) & _INSTANCE_FORBIDDEN:
        raise InstallError(f"实例名「{text}」里有不能用的字符")
    if text.endswith(".") or text.endswith(" "):
        raise InstallError("实例名结尾不能是点或空格")
    return text


@dataclass(frozen=True)
class RemoteVersion:
    id: str
    type: str
    url: str
    sha1: str | None = None
    release_time: str | None = None


@dataclass
class InstallReport:
    version_id: str
    version: VersionJson | None = None
    downloads: DownloadReport = field(default_factory=DownloadReport)
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.downloads.ok and self.version is not None


def manifest_url(sources: Iterable[Source] | None = None) -> str:
    return MANIFEST_URL


def fetch_manifest(fetcher: Fetcher) -> dict:
    raw = fetcher.get_bytes(MANIFEST_URL, label="version_manifest_v2.json", ttl=600)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"version_manifest 不是合法 JSON：{exc}") from exc


def remote_versions(fetcher: Fetcher, kind: str | None = None) -> list[RemoteVersion]:
    """从官方 manifest 里列出可选版本；``kind`` 可过滤 release / snapshot。"""
    data = fetch_manifest(fetcher)
    result = []
    for entry in data.get("versions", []):
        if kind and entry.get("type") != kind:
            continue
        version_id = entry.get("id")
        url = entry.get("url")
        if not isinstance(version_id, str) or not isinstance(url, str):
            continue
        result.append(
            RemoteVersion(
                id=version_id,
                type=str(entry.get("type", "")),
                url=url,
                sha1=entry.get("sha1"),
                release_time=entry.get("releaseTime"),
            )
        )
    return result


def find_remote(fetcher: Fetcher, version_id: str) -> RemoteVersion:
    for entry in remote_versions(fetcher):
        if entry.id == version_id:
            return entry
    raise InstallError(
        f"官方版本列表里没有 {version_id!r}。用 `ge available` 看看有哪些可选。"
    )


def ensure_launcher_profiles(layout: GameLayout) -> None:
    """installer（Fabric/Forge）会读这个文件，缺了就写一个空壳。"""
    path = layout.launcher_profiles_path
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EMPTY_PROFILES, ensure_ascii=False, indent=2), encoding="utf-8")


def _notify(hook: ProgressHook | None, stage: str, done: int, total: int, message: str) -> None:
    if hook is not None:
        hook(stage, done, total, message)


def _library_jobs(
    layout: GameLayout, version: VersionJson, env: Environment
) -> list[DownloadJob]:
    """版本里所有需要下载的库（含 natives），按规则过滤掉别的平台。"""
    jobs: list[DownloadJob] = []
    for library in version.libraries:
        if not allow(library.rules, env):
            continue
        artifact = library.artifact
        if artifact is None:
            artifact = libraries.synthetic_artifact(library)
        candidates = []
        if artifact is not None and artifact.url:
            candidates.append(artifact)
        # 老格式 natives：classifier 里的文件也要下
        native, _classifier = libraries.native_artifact(library, env)
        if native is not None and native is not artifact and native.url:
            candidates.append(native)
        for item in candidates:
            relative = libraries.artifact_relative_path(library, item)
            urls = [item.url] if item.url else []
            if not urls:
                urls = [LIBRARIES_BASE + relative]
            jobs.append(
                DownloadJob(
                    urls=urls,
                    destination=layout.libraries_dir / relative,
                    sha1=item.sha1,
                    size=item.size,
                    label=relative,
                )
            )
    return jobs


def _asset_jobs(layout: GameLayout, asset_index_id: str) -> list[DownloadJob]:
    index_path = layout.asset_index_path(asset_index_id)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    objects = data.get("objects")
    if not isinstance(objects, dict):
        raise InstallError(f"资源索引格式不对（没有 objects）：{index_path}")
    jobs = []
    for name, meta in objects.items():
        digest = meta.get("hash")
        if not isinstance(digest, str):
            raise InstallError(f"资源索引条目缺少 hash：{name}")
        jobs.append(
            DownloadJob(
                urls=[f"{ASSETS_BASE}/{digest[:2]}/{digest}"],
                destination=layout.asset_object_path(digest),
                sha1=digest,
                size=meta.get("size"),
                label=name,
            )
        )
    return jobs


def install_version(
    layout: GameLayout,
    version_id: str,
    *,
    instance_name: str | None = None,
    fetcher: Fetcher | None = None,
    with_assets: bool = True,
    env: Environment | None = None,
    progress: ProgressHook | None = None,
    sources: Iterable[Source] | None = None,
) -> InstallReport:
    """安装一个原版版本到一个实例。

    实例名 = ``versions/<实例>/`` 的文件夹名，默认和版本号相同。
    同名实例已经存在时抛 ``InstanceExistsError``（不再覆盖，和 HMCL 一样）。
    """
    instance = validate_instance_name(instance_name or version_id)
    if layout.instance_exists(instance) or layout.version_dir(instance).exists():
        raise InstanceExistsError(f"实例「{instance}」已经存在")

    fetcher = fetcher or Fetcher(sources=tuple(sources) if sources else DEFAULT_SOURCES)
    if fetcher.cache_dir is None:
        fetcher = replace(fetcher, cache_dir=layout.root / "launcher" / "cache" / "meta")
    env = env or Environment()
    report = InstallReport(version_id=instance)

    _notify(progress, "manifest", 0, 1, "读取版本列表…")
    remote = find_remote(fetcher, version_id)

    layout.versions_dir.mkdir(parents=True, exist_ok=True)
    layout.libraries_dir.mkdir(parents=True, exist_ok=True)
    layout.assets_dir.mkdir(parents=True, exist_ok=True)
    layout.indexes_dir.mkdir(parents=True, exist_ok=True)
    layout.objects_dir.mkdir(parents=True, exist_ok=True)
    layout.log_configs_dir.mkdir(parents=True, exist_ok=True)
    ensure_launcher_profiles(layout)

    # ---- 版本 JSON ----
    _notify(progress, "version", 0, 1, f"下载 {version_id} 的版本 JSON…")
    version_json_path = layout.version_json_path(instance)
    result = fetcher.download(
        DownloadJob(
            urls=[remote.url],
            destination=version_json_path,
            sha1=remote.sha1,
            label=f"{instance}.json",
        )
    )
    report.downloads.merge(result)
    if not version_json_path.is_file():
        report.messages.append(f"版本 JSON 下载失败：{result.failures}")
        return report

    # 实例名作为 id，真实版本号记在 clientVersion 里（显示"版本"列时用）
    raw = json.loads(version_json_path.read_text(encoding="utf-8-sig"))
    raw["id"] = instance
    raw["clientVersion"] = version_id
    version_json_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    version = VersionJson.from_dict(raw, f"{instance}.json")
    report.version = version

    # ---- 客户端 jar（放在实例自己的目录里，实例之间互不影响）----
    jar_id = instance
    client = version.downloads.get("client")
    if client is None or not client.url:
        report.messages.append("版本 JSON 里没有 downloads.client，跳过客户端 jar")
    else:
        _notify(progress, "client", 0, 1, "下载客户端 jar…")
        result = fetcher.download(
            DownloadJob(
                urls=[client.url],
                destination=layout.version_jar_path(jar_id),
                sha1=client.sha1,
                size=client.size,
                label=f"{jar_id}.jar",
            )
        )
        report.downloads.merge(result)

    # ---- 库 ----
    jobs = _library_jobs(layout, version, env)
    _notify(progress, "libraries", 0, len(jobs), f"下载 {len(jobs)} 个库…")
    done = 0

    def _lib_done(job: DownloadJob, _report: DownloadReport) -> None:
        nonlocal done
        done += 1
        _notify(progress, "libraries", done, len(jobs), job.describe())

    result = fetcher.download_all(jobs, on_done=_lib_done)
    report.downloads.merge(result)

    # ---- 资源 ----
    if version.asset_index is not None:
        index = version.asset_index
        _notify(progress, "assets_index", 0, 1, f"下载资源索引 {index.id}…")
        result = fetcher.download(
            DownloadJob(
                urls=[index.url] if index.url else [],
                destination=layout.asset_index_path(index.id),
                sha1=index.sha1,
                size=index.size,
                label=f"indexes/{index.id}.json",
            )
        )
        report.downloads.merge(result)
        if with_assets and layout.asset_index_path(index.id).is_file():
            jobs = _asset_jobs(layout, index.id)
            total = len(jobs)
            _notify(progress, "assets", 0, total, f"下载 {total} 个资源文件…（这一步最慢）")
            done = 0

            def _asset_done(job: DownloadJob, _report: DownloadReport) -> None:
                nonlocal done
                done += 1
                if done % 50 == 0 or done == total:
                    _notify(progress, "assets", done, total, job.describe())

            # 资源文件数量多、单个很小：镜像优先 + 关赛跑 + keep-alive，别每文件都握手
            asset_fetcher = for_bulk_assets(fetcher)
            report.downloads.merge(asset_fetcher.download_all(jobs, on_done=_asset_done))
    else:
        report.messages.append("版本 JSON 里没有 assetIndex，跳过资源下载")

    # ---- 日志配置 ----
    if version.logging is not None and version.logging.file.url:
        log_file = version.logging.file
        _notify(progress, "logging", 0, 1, "下载日志配置…")
        report.downloads.merge(
            fetcher.download(
                DownloadJob(
                    urls=[log_file.url],
                    destination=layout.log_config_path(log_file.id),
                    sha1=log_file.sha1,
                    size=log_file.size,
                    label=f"log_configs/{log_file.id}",
                )
            )
        )

    _notify(
        progress,
        "done",
        1,
        1,
        f"完成：下载 {report.downloads.downloaded} 个，跳过 {report.downloads.skipped} 个",
    )
    return report


def missing_report(report: InstallReport, limit: int = 10) -> str:
    """把失败列表整理成给人看的文字。"""
    failures = report.downloads.failures
    if not failures:
        return ""
    lines = [f"  - {name}：{reason}" for name, reason in failures[:limit]]
    more = f"\n  …… 还有 {len(failures) - limit} 个" if len(failures) > limit else ""
    return "\n".join(lines) + more


def version_exists(layout: GameLayout, version_id: str) -> bool:
    return layout.version_json_path(version_id).is_file()


def load_local_version(layout: GameLayout, version_id: str) -> VersionJson | None:
    try:
        return VersionJson.load(layout.version_json_path(version_id))
    except VersionJsonError:
        return None


def remove_version(layout: GameLayout, version_id: str) -> None:
    """删掉一个版本目录（只动 versions/<id>，不碰共享的 libraries/assets）。"""
    import shutil

    target = layout.version_dir(version_id)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def asset_objects_missing(layout: GameLayout, asset_index_id: str, limit: int = 5) -> list[str]:
    """检查资源索引里有没有缺的文件（启动前体检用）。"""
    index_path = layout.asset_index_path(asset_index_id)
    if not index_path.is_file():
        return [f"assets/indexes/{asset_index_id}.json"]
    data = json.loads(index_path.read_text(encoding="utf-8"))
    missing: list[str] = []
    for name, meta in (data.get("objects") or {}).items():
        digest = meta.get("hash", "")
        if not layout.asset_object_path(digest).is_file():
            missing.append(name)
            if len(missing) >= limit:
                break
    return missing


def library_files(layout: GameLayout, version: VersionJson, env: Environment) -> list[Path]:
    """该版本在当前平台上需要的所有库文件（供体检/补全用）。"""
    paths = []
    for job in _library_jobs(layout, version, env):
        paths.append(job.destination)
    return paths


def iter_asset_objects(layout: GameLayout, asset_index_id: str) -> Iterable[tuple[str, str, int | None]]:
    index_path = layout.asset_index_path(asset_index_id)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    for name, meta in (data.get("objects") or {}).items():
        yield name, meta.get("hash", ""), meta.get("size")
