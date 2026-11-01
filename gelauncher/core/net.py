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
import http.client
import io
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


from .cancel import Cancelled


class DownloadError(RuntimeError):
    """所有源都试过了还是下不下来。"""


class _Cancelled(Exception):
    """并行赛跑里被别的源抢先了，主动放弃。"""


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
        # NeoForge：镜像保留了官方的 /releases 段（实测 /maven/org/... 会卡住，
        # /maven/releases/org/... 才是 200），所以这里只换 host、不动路径
        ("maven.neoforged.net", "https://bmclapi2.bangbang93.com/maven"),
    ),
    allow_insecure_tls=True,
)

DEFAULT_SOURCES: tuple[Source, ...] = (OFFICIAL, BMCLAPI)
MIRROR_FIRST_SOURCES: tuple[Source, ...] = (BMCLAPI, OFFICIAL)

#: 设置界面里的"下载源"三选项
SOURCE_MODES = ("auto", "official", "mirror")
SOURCE_MODE_LABELS = {
    "auto": "自动（官方优先，慢/不通时自动换镜像，小请求并行赛跑）",
    "official": "仅官方（Mojang）",
    "mirror": "仅镜像（BMCLAPI）",
}


def sources_for(mode: str) -> tuple[Source, ...]:
    mode = (mode or "auto").strip().lower()
    if mode == "official":
        return (OFFICIAL,)
    if mode == "mirror":
        return (BMCLAPI,)
    return DEFAULT_SOURCES


def for_bulk_assets(fetcher: "Fetcher") -> "Fetcher":
    """下载几千个资源小文件时用的下载器：镜像优先、关掉赛跑。

    资源文件每个只有几 KB，赛跑意味着每个文件都多一条连接；
    BMCLAPI 在这种高频并发下会握手超时。镜像优先 + keep-alive 才能跑满。
    用户显式选了"仅官方"时保持原样。
    """
    from dataclasses import replace as _replace

    if len(fetcher.sources) <= 1:
        return _replace(fetcher, race=False)
    # 约定：镜像排在官方后面；资源文件优先走镜像
    return _replace(fetcher, sources=tuple(reversed(fetcher.sources)), race=False)


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


_SSL_SECURE = _ssl_context(False)
_SSL_INSECURE = _ssl_context(True)

#: 每个线程一套 keep-alive 连接（和 HMCL 的 HTTP 客户端一样复用 TLS）
_CONN_LOCAL = threading.local()


class _KeepAliveResponse:
    """把 http.client 的响应包一层，让调用方可以照旧 ``with _open(...) as r``。"""

    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse):
        self._connection = connection
        self._response = response
        self.status = response.status
        self.headers = response.headers

    def read(self, amount: int | None = None):
        try:
            return self._response.read(amount)
        except (http.client.HTTPException, OSError):
            _drop_connection(self._connection)
            raise

    def close(self) -> None:
        try:
            # 没读完的 body 不排空的话，这条连接不能复用
            if not self._response.isclosed():
                self._response.read()
        except (http.client.HTTPException, OSError):
            _drop_connection(self._connection)

    def __enter__(self) -> "_KeepAliveResponse":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _connections() -> dict[tuple[str, int, bool], http.client.HTTPConnection]:
    pool = getattr(_CONN_LOCAL, "pool", None)
    if pool is None:
        pool = {}
        _CONN_LOCAL.pool = pool
    return pool


def _drop_connection(connection: http.client.HTTPConnection) -> None:
    pool = _connections()
    for key, value in list(pool.items()):
        if value is connection:
            pool.pop(key, None)
            break
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def _connection(
    scheme: str, host: str, port: int, timeout: float, insecure: bool
) -> http.client.HTTPConnection:
    key = (host, port, insecure)
    pool = _connections()
    connection = pool.get(key)
    if connection is not None and connection.sock is None:
        # 服务端已经关了（Connection: close），重新建一条
        pool.pop(key, None)
        connection = None
    if connection is None:
        if scheme == "https":
            connection = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=_SSL_INSECURE if insecure else _SSL_SECURE
            )
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        pool[key] = connection
    else:
        connection.timeout = timeout
    return connection


def _open(url: str, timeout: float, insecure: bool):
    """GET 一个 URL，尽量复用已有连接（每个线程、每台主机一条）。"""
    current = url
    for _ in range(6):
        parts = urllib.parse.urlsplit(current)
        scheme = parts.scheme.lower()
        host = parts.hostname or ""
        port = parts.port or (443 if scheme == "https" else 80)
        connection = _connection(scheme, host, port, timeout, insecure)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            connection.request(
                "GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
            )
            response = connection.getresponse()
        except Exception:
            _drop_connection(connection)
            raise

        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            try:
                response.read()
            except (http.client.HTTPException, OSError):
                _drop_connection(connection)
            if not location:
                return _KeepAliveResponse(connection, response)
            current = urllib.parse.urljoin(current, location)
            continue

        if response.status >= 400:
            try:
                body = response.read()
            except (http.client.HTTPException, OSError):
                body = b""
                _drop_connection(connection)
            raise urllib.error.HTTPError(
                current, response.status, response.reason, response.headers, io.BytesIO(body)
            )

        return _KeepAliveResponse(connection, response)

    raise DownloadError(f"重定向次数过多：{url}")


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


# --------------------------------------------------------------------------- #
# 源健康度（进程级缓存，带 TTL）
# ---------------------------------------------------------------------------

_HEALTH_LOCK = threading.Lock()
#: (source.name, host) -> (ok, 时间戳)
_HEALTH: dict[tuple[str, str], tuple[bool, float]] = {}
HEALTH_OK_TTL = 600.0     # 10 分钟内认为"这个源在这台主机上是好的"
HEALTH_BAD_TTL = 180.0    # 3 分钟内不再优先用

_DEAD_LOCK = threading.Lock()
#: (source.name, host) → 错误信息；进程级，跨 Fetcher 共享
_DEAD: dict[tuple[str, str], str] = {}


def _dead_get(key: tuple[str, str]) -> str | None:
    with _DEAD_LOCK:
        return _DEAD.get(key)


def _dead_set(key: tuple[str, str], reason: str) -> None:
    with _DEAD_LOCK:
        _DEAD[key] = reason


def _dead_all() -> dict[tuple[str, str], str]:
    with _DEAD_LOCK:
        return dict(_DEAD)


def _health_get(key: tuple[str, str]) -> bool | None:
    with _HEALTH_LOCK:
        entry = _HEALTH.get(key)
        if entry is None:
            return None
        ok, stamp = entry
        ttl = HEALTH_OK_TTL if ok else HEALTH_BAD_TTL
        if time.time() - stamp > ttl:
            _HEALTH.pop(key, None)
            return None
        return ok


def _health_ok_at(key: tuple[str, str]) -> float | None:
    """如果这个源最近成功过，返回成功时间戳（用于排序）。"""
    with _HEALTH_LOCK:
        entry = _HEALTH.get(key)
        if entry is None:
            return None
        ok, stamp = entry
        if not ok or time.time() - stamp > HEALTH_OK_TTL:
            return None
        return stamp


def _health_set(key: tuple[str, str], ok: bool) -> None:
    with _HEALTH_LOCK:
        _HEALTH[key] = (ok, time.time())


def reset_health_cache() -> None:
    """测试用：清掉进程级健康度 / 死亡记录缓存。"""
    with _HEALTH_LOCK:
        _HEALTH.clear()
    with _DEAD_LOCK:
        _DEAD.clear()


@dataclass
class Fetcher:
    """按源下载文件。

    "自动"模式的核心是两件事：

    1. **小型请求直接并行赛跑**（``get_bytes``）：官方和镜像同时发，谁先回来用谁。
       实测 ``meta.fabricmc.net`` 官方经常要等满 15~20 秒才超时，赛跑能把这一步压到 1 秒。
    2. **按主机记住健康度**：某个源在某台主机上成功/失败会存进进程级缓存（带 TTL），
       后面的文件直接按"最近成功"排序，失败的源暂时排到最后，不用每个文件都重新试错。
       缓存是模块级的，所以连续装好几个东西也不会重复踩坑。
    """

    sources: Sequence[Source] = DEFAULT_SOURCES
    timeout: float = 30.0
    retries: int = 3
    threads: int = 8
    progress: ProgressCallback | None = None
    probe_timeout: float = 5.0
    #: 小请求是否多源并行赛跑（关掉就退回顺序重试，测试里可能用得到）
    race: bool = True
    #: 元数据磁盘缓存目录（None 表示不缓存）
    cache_dir: Path | None = None

    _good_source: str | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ 排序

    @staticmethod
    def _host_of(url: str) -> str:
        return url.split("//", 1)[-1].split("/", 1)[0].lower()

    def _probe(self, source: Source, url: str) -> bool:
        """只做 TCP + TLS 握手，判断这台主机到底通不通（结果进全局缓存）。"""
        key = (source.name, self._host_of(url))
        known = _health_get(key)
        if known is not None:
            return known
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
        _health_set(key, ok)
        return ok

    def _ordered_sources(self, url: str) -> list[Source]:
        """排序规则：健康的按配置顺序，没试过的其次，最近失败的最后。

        不在健康源之间按"谁最后成功"排序——并行赛跑时两个源都会成功，
        那样排序会随机抖动；真正慢/不通的源会因为失败被挪到后面。
        """
        host = self._host_of(url)
        scored: list[tuple[int, int, Source]] = []
        skipped: list[Source] = []
        for index, source in enumerate(self.sources):
            host_key = (source.name, host)
            if _dead_get(host_key):
                skipped.append(source)
                continue
            # 赛跑模式不做可达性探测：探测本身要等 5 秒（官方 maven 就是死在这里），
            # 而赛跑已经保证慢源不会拖后腿；只有顺序模式才值得先探一下。
            if not self.race and not self._probe(source, url):
                skipped.append(source)
                continue
            ok_at = _health_ok_at(host_key)
            tried = _health_get(host_key)
            if ok_at is not None:
                rank = 0          # 最近成功过
            elif tried is None:
                rank = 1          # 还没试过
            else:
                rank = 2          # 试过但失败了（不在 _dead 里，属于非传输层失败）
            scored.append((rank, index, source))
        scored.sort(key=lambda item: (item[0], item[1]))
        alive = [item[2] for item in scored]
        return alive or skipped

    def _note_success(self, source: Source, url: str) -> None:
        _health_set((source.name, self._host_of(url)), True)
        with self._lock:
            self._good_source = source.name

    def _note_failure(self, source: Source, url: str, exc: BaseException) -> None:
        if not _is_transport_error(exc):
            return
        host = self._host_of(url)
        _health_set((source.name, host), False)
        _dead_set((source.name, host), f"{type(exc).__name__}: {exc}")

    def dead_sources(self) -> dict[tuple[str, str], str]:
        names = {source.name for source in self.sources}
        return {key: reason for key, reason in _dead_all().items() if key[0] in names}

    # ------------------------------------------------------------------ 小文件

    def _get_once(self, source: Source, url: str, errors: list[str]) -> bytes | None:
        candidate = source.rewrite(url)
        for attempt in range(self.retries):
            try:
                with _open(candidate, self.timeout, False) as response:
                    data = response.read()
                self._note_success(source, url)
                return data
            except Exception as exc:  # noqa: BLE001 - 要按错误类型分类处理
                self._note_failure(source, url, exc)
                if _is_cert_error(exc) and source.allow_insecure_tls:
                    try:
                        with _open(candidate, self.timeout, True) as response:
                            data = response.read()
                        self._note_success(source, url)
                        return data
                    except Exception as inner:  # noqa: BLE001
                        self._note_failure(source, url, inner)
                        exc = inner
                if attempt == self.retries - 1:
                    errors.append(f"{source.name} {candidate} → {exc}")
                time.sleep(min(2**attempt * 0.5, 4))
        return None

    def get_bytes(self, url: str, *, label: str = "", ttl: float = 0.0) -> bytes:
        """取一个小文件。

        ``ttl`` > 0 且设置了 ``cache_dir`` 时走磁盘缓存（HMCL 的 cache repository 做法）：
        元数据类请求基本是读本地，重复安装/查版本几乎不花时间。
        """
        cached = self._cache_read(url, ttl)
        if cached is not None:
            return cached

        sources = self._ordered_sources(url)
        if self.race and len(sources) > 1:
            data = self._get_bytes_racing(url, sources, label)
        else:
            errors: list[str] = []
            data = None
            for source in sources:
                data = self._get_once(source, url, errors)
                if data is not None:
                    break
            if data is None:
                raise DownloadError(f"下载失败 {label or url}：{' | '.join(errors)}")
        self._cache_write(url, data)
        return data

    # ------------------------------------------------------------------ 缓存

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return Path(self.cache_dir) / f"{key}.meta"

    def _cache_read(self, url: str, ttl: float) -> bytes | None:
        if ttl <= 0:
            return None
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            if time.time() - path.stat().st_mtime > ttl:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _cache_write(self, url: str, data: bytes) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass

    def _get_bytes_racing(self, url: str, sources: Sequence[Source], label: str) -> bytes:
        """多个源同时请求，谁先回来用谁。慢的那几个直接丢后台晾着。"""
        errors: list[str] = []
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(sources))
        futures = {
            pool.submit(self._get_once, source, url, errors): source for source in sources
        }
        try:
            for future in concurrent.futures.as_completed(futures):
                try:
                    data = future.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    continue
                if data is not None:
                    for other in futures:
                        other.cancel()
                    return data
        finally:
            pool.shutdown(wait=False)
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

        # 和 HMCL 一样：候选 URL 并发抢，谁先下完用谁。
        # 之前是"先官方、超时再镜像"，光等官方超时就能把 8 个小库拖成三分钟。
        candidates = [
            (job_url, source)
            for job_url in job.urls
            for source in self._ordered_sources(job_url)
        ]
        if self.race and len(candidates) > 1:
            raced = self._download_racing(job, destination, candidates)
            if raced is not None:
                return raced

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
                            self._note_success(source, job_url)
                            return report
                        except Exception as exc:  # noqa: BLE001
                            part.unlink(missing_ok=True)
                            self._note_failure(source, job_url, exc)
                            if attempt == self.retries - 1:
                                errors.append(f"{source.name} {url} → {exc}")
                            time.sleep(min(2**attempt * 0.5, 4))

        report.failures.append((job.describe(), " | ".join(errors) or "未知错误"))
        return report

    def _download_racing(
        self,
        job: DownloadJob,
        destination: Path,
        candidates: Sequence[tuple[str, Source]],
    ) -> DownloadReport | None:
        """多个候选 URL 同时下载同一个文件，第一个校验通过的采用。

        每个候选写自己的 ``.part``，只有校验通过后才去抢 ``os.replace``；
        抢输的（或失败的）自己清理临时文件。这里不用"取消事件"互相打断，
        免得把内部异常混进真正的错误列表。
        """
        errors: list[str] = []
        lock = threading.Lock()
        winners: list[tuple[Source, str, int]] = []

        def attempt(candidate: tuple[str, Source]) -> bool:
            job_url, source = candidate
            url = source.rewrite(job_url)
            part = destination.with_name(f"{destination.name}.{abs(hash((source.name, url)))}.part")
            part.unlink(missing_ok=True)
            try:
                written = self._stream_to(url, part, job, source)
                if not verify(part, job.sha1, job.size):
                    raise DownloadError("校验失败（sha1 不匹配）")
                with lock:
                    if winners:
                        part.unlink(missing_ok=True)
                        return False
                    try:
                        os.replace(part, destination)
                    except OSError:
                        part.unlink(missing_ok=True)
                        raise
                    winners.append((source, job_url, written))
                return True
            except Exception as exc:  # noqa: BLE001
                part.unlink(missing_ok=True)
                with lock:
                    errors.append(f"{source.name} {url} → {exc}")
                self._note_failure(source, job_url, exc)
                return False

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(candidates))
        futures = [pool.submit(attempt, candidate) for candidate in candidates]
        try:
            concurrent.futures.wait(futures)
        finally:
            pool.shutdown(wait=False)

        if winners:
            source, job_url, written = winners[0]
            self._note_success(source, job_url)
            report = DownloadReport()
            report.downloaded = 1
            report.bytes_written = written
            return report
        if errors:
            report = DownloadReport()
            report.failures.append((job.describe(), " | ".join(errors)))
            return report
        return None

    def _stream_to(
        self,
        url: str,
        part: Path,
        job: DownloadJob,
        source: Source,
        should_stop: Callable[[], bool] | None = None,
    ) -> int:
        written = 0
        insecure = False
        while True:
            try:
                with _open(url, self.timeout, insecure) as response:
                    total = job.size or int(response.headers.get("Content-Length") or 0)
                    with part.open("wb") as sink:
                        while True:
                            if should_stop is not None and should_stop():
                                raise _Cancelled("别的源先完成了")
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

    def download_all(
        self,
        jobs: Iterable[DownloadJob],
        on_done: Callable[[DownloadJob, DownloadReport], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> DownloadReport:
        total = DownloadReport()
        job_list = list(jobs)
        if not job_list:
            return total

        def worker(job: DownloadJob) -> DownloadReport:
            if should_stop is not None and should_stop():
                raise Cancelled("任务已取消")
            try:
                return self.download(job)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - 单个失败不能拖垮整批
                failed = DownloadReport()
                failed.failures.append((job.describe(), str(exc)))
                return failed

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.threads))
        futures = {pool.submit(worker, job): job for job in job_list}
        try:
            for future in concurrent.futures.as_completed(futures):
                if should_stop is not None and should_stop():
                    for pending in futures:
                        pending.cancel()
                    raise Cancelled("任务已取消")
                job = futures[future]
                try:
                    report = future.result()
                except Cancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
                total.merge(report)
                if on_done is not None:
                    on_done(job, report)
        finally:
            pool.shutdown(wait=False)
        return total


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
