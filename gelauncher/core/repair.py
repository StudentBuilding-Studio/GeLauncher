"""补全一个实例缺的文件：库 / assets / 客户端 jar / 日志配置。

黑屏、没声音、进不去世界，十有八九是 assets 或库没下全（比如安装时用了 --no-assets，
或者别的启动器装到一半）。这里按 sha1 只补缺的那部分。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import install_vanilla as iv
from .layout import GameLayout
from .models import VersionJsonError
from .net import DownloadJob, DownloadReport, Fetcher, for_bulk_assets
from .rules import Environment


class RepairError(RuntimeError):
    """没有可补的东西，或者版本 JSON 读不了。"""


@dataclass
class RepairReport:
    instance: str
    downloads: DownloadReport

    @property
    def ok(self) -> bool:
        return not self.downloads.failures


def _asset_jobs(layout: GameLayout, index_path) -> list[DownloadJob]:
    """把资源索引展开成下载任务（与安装时同一套逻辑）。"""
    return list(iv._asset_jobs(layout, index_path))


def repair_instance(
    layout: GameLayout,
    instance: str,
    *,
    fetcher: Fetcher,
    env: Environment | None = None,
    progress=None,
) -> RepairReport:
    env = env or Environment()
    try:
        version = layout.load_json(instance)
    except VersionJsonError as exc:
        raise RepairError(f"读不了 {instance} 的版本 JSON：{exc}") from exc

    jobs: list[DownloadJob] = []

    # 客户端 jar：实例目录里没有就补
    jar = version.jar or instance
    if not layout.version_jar_path(jar).is_file():
        client = version.downloads.get("client")
        if client and client.url:
            jobs.append(
                DownloadJob(
                    urls=[client.url],
                    destination=layout.version_jar_path(jar),
                    sha1=client.sha1,
                    size=client.size,
                    label=f"{jar}.jar",
                )
            )

    # 库
    jobs.extend(iv._library_jobs(layout, version, env))

    # assets 索引 + 对象
    if version.asset_index is not None:
        index = version.asset_index
        index_path = layout.asset_index_path(index.id)
        if not index_path.is_file() and index.url:
            jobs.append(
                DownloadJob(
                    urls=[index.url],
                    destination=index_path,
                    sha1=index.sha1,
                    size=index.size,
                    label=f"indexes/{index.id}.json",
                )
            )
        if index_path.is_file():
            jobs.extend(iv._asset_jobs(layout, index.id))

    # 日志配置
    if version.logging is not None and version.logging.file.url:
        log_file = version.logging.file
        jobs.append(
            DownloadJob(
                urls=[log_file.url],
                destination=layout.log_config_path(log_file.id),
                sha1=log_file.sha1,
                size=log_file.size,
                label=f"log_configs/{log_file.id}",
            )
        )

    if not jobs:
        raise RepairError("没有缺的文件")

    done = 0

    def on_done(job: DownloadJob, _report: DownloadReport) -> None:
        nonlocal done
        done += 1
        if progress is not None and (done % 200 == 0):
            progress("repair", done, len(jobs), job.describe())

    report = fetcher.download_all(jobs, on_done=on_done)
    return RepairReport(instance=instance, downloads=report)
