"""下载器：并行赛跑 + 源健康度缓存 + 下载源模式。"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gelauncher.core import net
from gelauncher.core.net import BMCLAPI, OFFICIAL, Fetcher, Source, sources_for


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        server: "Server" = self.server  # type: ignore[assignment]
        path = self.path.split("?")[0]
        delay = server.delays.get(path, 0)
        if delay:
            time.sleep(delay)
        data = server.routes.get(path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.routes: dict[str, bytes] = {}
        self.delays: dict[str, float] = {}


@pytest.fixture
def server():
    srv = Server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    net.reset_health_cache()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        net.reset_health_cache()


def _source(port: int, name: str, prefix: str) -> Source:
    base = f"http://127.0.0.1:{port}{prefix}"
    return Source(
        name=name,
        host_map=(
            ("piston-meta.mojang.com", base),
            ("meta.fabricmc.net", base),
        ),
    )


def test_racing_uses_the_fastest_source(server):
    server.routes["/slow/x.json"] = b"SLOW"
    server.routes["/fast/x.json"] = b"FAST"
    server.delays["/slow/x.json"] = 6.0
    slow = _source(server.server_address[1], "official", "/slow")
    fast = _source(server.server_address[1], "bmclapi", "/fast")

    fetcher = Fetcher(sources=(slow, fast), retries=1, timeout=20)
    start = time.time()
    data = fetcher.get_bytes("https://piston-meta.mojang.com/x.json")
    elapsed = time.time() - start
    assert data == b"FAST"
    assert elapsed < 3, f"应该靠镜像先回来，实际 {elapsed:.1f}s"


def test_health_cache_prefers_source_that_worked(server):
    server.routes["/a/x.json"] = b"A"
    server.routes["/b/x.json"] = b"B"
    a = _source(server.server_address[1], "official", "/a")
    b = _source(server.server_address[1], "bmclapi", "/b")

    fetcher = Fetcher(sources=(a, b), retries=1, timeout=5, race=False)
    # 关掉赛跑，验证顺序逻辑：配置里第一个源先用
    assert fetcher.get_bytes("https://piston-meta.mojang.com/x.json") == b"A"
    ordered = fetcher._ordered_sources("https://piston-meta.mojang.com/x.json")
    assert ordered[0].name == "official"

    fetcher2 = Fetcher(sources=(a, b), retries=1, timeout=5, race=False)
    fetcher2._note_failure(a, "https://piston-meta.mojang.com/x.json", OSError("boom"))
    ordered2 = fetcher2._ordered_sources("https://piston-meta.mojang.com/x.json")
    assert ordered2[0].name == "bmclapi", "失败的源要排到后面"


def test_sources_for_modes():
    assert sources_for("official") == (OFFICIAL,)
    assert sources_for("mirror") == (BMCLAPI,)
    assert sources_for("auto") == (OFFICIAL, BMCLAPI)
    # 乱值回落到 auto
    assert sources_for("nonsense") == (OFFICIAL, BMCLAPI)


def test_download_source_normalized(tmp_path):
    from gelauncher.config import Config

    config = Config.load(tmp_path / "missing.json")
    config.download_source = "weird"
    config._normalize()
    assert config.download_source == "auto"

    config.download_source = "mirror"
    config._normalize()
    assert config.download_source == "mirror"
