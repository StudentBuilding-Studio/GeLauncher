"""下载器：重试、多源切换、sha1 校验、先写 .part 再改名。

设计约束（方案 §7「下载完整性」+ 实测到的镜像抽风）：
- 源不能按"整站可用/不可用"判断，必须**按文件**降级：同一个文件先试官方，
  失败再试镜像；
- 下完必须校验 sha1，校验不过就当失败重下，绝不把半个文件留在正式路径上；
- 所有下载都先写 ``<name>.part``，成功后再 ``os.replace``；
- 镜像的 TLS 链偶尔是坏的（实测 bmclapi 会 CERTIFICATE_VERIFY_FAILED），
  对**镜像**允许降级成"不校验证书 + sha1 兜底"，官方源永远校验证书。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

USER_AGENT = "gelauncher/0.1 (+https://example.invalid)"
CHUNK = 64 * 1024


class DownloadError(RuntimeError):
    """所有源都试过了还是下不下来。"""


@dataclass(frozen=True)
class Source:
    """一个下载源：名字 + 把官方 URL 改写成这个源 URL 的规则。"""

    name: str
    host_map: tuple[tuple[str, str], ...] = ()
    allow_insecure_tls: bool = False

    def rewrite(self, url: str) -> str:
        for original, replacement in self.host_map:
            prefix = "https://" + original
            if url.startswith(prefix):
                return replacement.rstrip("/") + url[len(prefix) :]
        return url


#: 官方源：什么都不改
OFFICIAL = Source(name="official")
#: BMCLAPI：路径基本同构，libraries / assets 走它自己的前缀
BMCLAPI = Source(
    name="bmclapi",
    host_map=(
        ("piston-meta.mojang.com", "https://bmclapi2.bangbang93.com"),
        ("launchermeta.mojang.com", "https://bmclapi2.bangbang93.com"),
        ("launcher.mojang.com", "https://bmclapi2.bangbang93.com"),
        ("piston-data.mojang.com", "https://bmclapi2.bangbang93.com"),
        ("libraries.minecraft.net", "https://bmclapi2.bangbang93.com/maven"),
        ("resources.download.minecraft.net", "https://bmclapi2.bangbang93.com/assets"),
        # Fabric 的 meta 在镜像上是另一个前缀（实测可用）
        ("meta.fabricmc.net", "https://bmclapi2.bangbang93.com/fabric-meta"),
        # 两个加载器的 Maven 仓库：镜像里有同一套坐标（实测 fabric-loader 1.14MB/s）
        ("maven.fabricmc.net", "https://bmclapi2.bangbang93.com/maven"),
        ("maven.minecraftforge.net", "https://bmclapi2.bangbang93.com/maven"),
    ),
    allow_insecure_tls=True,
)

DEFAULT_SOURCES: tuple[Source, ...] = (OFFICIAL, BMCLAPI)
MIRROR_FIRST_SOURCES: tuple[Source, ...] = (BMCLAPI, OFFICIAL)


@dataclass
class DownloadJob:
    """一个待下载文件。``urls`` 已经按源的优先级排好序。"""

    urls: Sequence[str]
    destination: Path
    sha1: str | None = None
    size: int | None = None
    label: str = ""

    def describe(self) -> str:
        return self.label or self.destination.name


@dataclass
class DownloadReport:
    downloaded: int = 0
    skipped: int = 0
    bytes_written: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: "DownloadReport") -> None:
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.bytes_written += other.bytes_written
        self.failures.extend(other.failures)

    @property
    def ok(self) -> bool:
        return not self.failures


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK * 8), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, sha1: str | None, size: int | None = None) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    if sha1 is None:
        return True
    return sha1_of(path).lower() == sha1.lower()


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context()


def _open(url: str, timeout: float, insecure: bool):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context(insecure))


def _is_cert_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    text = str(reason).lower()
    return isinstance(reason, ssl.SSLError) or "certificate" in text


ProgressCallback = Callable[[int, int, str], None]


def _is_transport_error(exc: BaseException) -> bool:
    """连接层失败（超时 / TLS / 断连）——说明这条源在这台主机上压根不通。

    注意排除 HTTPError：404/500 是"这个文件没有/服务端报错"，
    换一条 URL 可能有救，但源本身还是可以用的。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, urllib.error.URLError)):
        return True
    return _is_cert_error(exc)


@dataclass
class Fetcher:
    """按源顺序下载文件。

    会自动学习"哪条源在哪台主机上不通"，并把最近一次成功的源排到前面：
    实测官方 maven 从这台机器连不上时，不这样做每个文件都要白等一个超时。
    第一次碰到某台主机时还会做一次 5 秒的 TLS 可达性探测，
    免得每个文件都付一次 15~20 秒的握手超时。
    """

    sources: Sequence[Source] = DEFAULT_SOURCES
    timeout: float = 30.0
    retries: int = 3
    threads: int = 8
    progress: ProgressCallback | None = None
    probe_timeout: float = 5.0

    #: (source.name, host) → 最后一次传输层错误
    _dead: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    #: (source.name, host) → 是否探测过可达
    _probed: dict[tuple[str, str], bool] = field(default_factory=dict, repr=False)
    _good_source: str | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ 排序

    @staticmethod
    def _host_of(url: str) -> str:
        return url.split("//", 1)[-1].split("/", 1)[0].lower()

    def _probe(self, source: Source, url: str) -> bool:
        """只做 TCP + TLS 握手，判断这台主机到底通不通（结果缓存）。"""
        key = (source.name, self._host_of(url))
        with self._lock:
            if key in self._probed:
                return self._probed[key]
        parsed = urllib.parse.urlsplit(source.rewrite(url))
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ok = False
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=self.probe_timeout)
            if parsed.scheme == "https":
                context = _ssl_context(insecure=True)
                wrapped = context.wrap_socket(sock, server_hostname=host)
                sock = None
                wrapped.close()
            ok = True
        except ssl.SSLError as exc:
            # 证书链坏了但主机是通的（实测 bmclapi 偶尔如此），仍算可达
            ok = _is_cert_error(exc) or isinstance(exc, ssl.SSLError)
        except OSError:
            ok = False
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._lock:
            self._probed[key] = ok
        return ok

    def _ordered_sources(self, url: str) -> list[Source]:
        host = self._host_of(url)
        alive: list[Source] = []
        skipped: list[Source] = []
        for source in self.sources:
            if (source.name, host) in self._dead:
                skipped.append(source)
                continue
            if not self._probe(source, url):
                with self._lock:
                    self._dead[(source.name, host)] = "可达性探测失败"
                skipped.append(source)
                continue
            alive.append(source)
        with self._lock:
            good = self._good_source
        if good:
            alive.sort(key=lambda source: 0 if source.name == good else 1)
        # 全部"不通"时也留一条试试，别直接把文件判死
        return alive or skipped

    def _note_success(self, source: Source) -> None:
        with self._lock:
            self._good_source = source.name

    def _note_failure(self, source: Source, url: str, exc: BaseException) -> None:
        if not _is_transport_error(exc):
            return
        with self._lock:
            self._dead[(source.name, self._host_of(url))] = f"{type(exc).__name__}: {exc}"
            if self._good_source == source.name:
                self._good_source = None

    def dead_sources(self) -> dict[tuple[str, str], str]:
        with self._lock:
            return dict(self._dead)

    # ------------------------------------------------------------------ 小文件

    def get_bytes(self, url: str, *, label: str = "") -> bytes:
        errors: list[str] = []
        for source in self._ordered_sources(url):
            candidate = source.rewrite(url)
            for attempt in range(self.retries):
                try:
                    with _open(candidate, self.timeout, False) as response:
                        data = response.read()
                    self._note_success(source)
                    return data
                except Exception as exc:  # noqa: BLE001 - 要按错误类型分类处理
                    self._note_failure(source, candidate, exc)
                    if _is_cert_error(exc) and source.allow_insecure_tls:
                        try:
                            with _open(candidate, self.timeout, True) as response:
                                data = response.read()
                            self._note_success(source)
                            return data
                        except Exception as inner:  # noqa: BLE001
                            self._note_failure(source, candidate, inner)
                            exc = inner
                    if attempt == self.retries - 1:
                        errors.append(f"{source.name} {candidate} → {exc}")
                    time.sleep(min(2**attempt * 0.5, 4))
        raise DownloadError(f"下载失败 {label or url}：{' | '.join(errors)}")

    # ------------------------------------------------------------------ 单文件

    def download(self, job: DownloadJob) -> DownloadReport:
        report = DownloadReport()
        destination = Path(job.destination)
        if verify(destination, job.sha1, job.size):
            report.skipped = 1
            report.bytes_written = destination.stat().st_size
            return report

        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        errors: list[str] = []

        for job_url in job.urls:
            for source in self._ordered_sources(job_url):
                for url in dict.fromkeys([source.rewrite(job_url)]):
                    for attempt in range(self.retries):
                        try:
                            written = self._stream_to(url, part, job, source)
                            if not verify(part, job.sha1, job.size):
                                raise DownloadError(f"校验失败（sha1 不匹配）：{job.describe()}")
                            os.replace(part, destination)
                            report.downloaded = 1
                            report.bytes_written = written
                            self._note_success(source)
                            return report
                        except Exception as exc:  # noqa: BLE001
                            part.unlink(missing_ok=True)
                            self._note_failure(source, url, exc)
                            if attempt == self.retries - 1:
                                errors.append(f"{source.name} {url} → {exc}")
                            time.sleep(min(2**attempt * 0.5, 4))

        report.failures.append((job.describe(), " | ".join(errors) or "未知错误"))
        return report

    def _stream_to(self, url: str, part: Path, job: DownloadJob, source: Source) -> int:
        written = 0
        insecure = False
        while True:
            try:
                with _open(url, self.timeout, insecure) as response:
                    total = job.size or int(response.headers.get("Content-Length") or 0)
                    with part.open("wb") as sink:
                        while True:
                            chunk = response.read(CHUNK)
                            if not chunk:
                                break
                            sink.write(chunk)
                            written += len(chunk)
                            if self.progress is not None:
                                self.progress(written, total, job.describe())
                    return written
            except Exception as exc:  # noqa: BLE001
                if not insecure and source.allow_insecure_tls and _is_cert_error(exc):
                    # 镜像证书链坏了：降级重试，靠 sha1 兜底
                    insecure = True
                    written = 0
                    part.unlink(missing_ok=True)
                    continue
                raise

    # ------------------------------------------------------------------ 批量

    def download_all(self, jobs: Iterable[DownloadJob], on_done: Callable[[DownloadJob, DownloadReport], None] | None = None) -> DownloadReport:
        total = DownloadReport()
        job_list = list(jobs)
        if not job_list:
            return total

        def worker(job: DownloadJob) -> DownloadReport:
            try:
                return self.download(job)
            except Exception as exc:  # noqa: BLE001 - 单个失败不能拖垮整批
                failed = DownloadReport()
                failed.failures.append((job.describe(), str(exc)))
                return failed

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.threads)) as pool:
            for job, report in zip(job_list, pool.map(worker, job_list)):
                total.merge(report)
                if on_done is not None:
                    on_done(job, report)
        return total


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
