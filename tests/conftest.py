"""测试用的公共工具和 fixture。"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
_WORKSPACE = Path(__file__).resolve().parent.parent
_COUNTER = itertools.count()


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def tmp_path(request) -> Path:
    """替代 pytest 自带的 tmp_path。

    自带版本用 ``tempfile.mkdtemp``（权限 0700），在 Windows 上会生成一个
    连自己都进不去的目录（本项目所在环境的沙箱里实测如此）。这里改成在
    工作区内用默认权限建目录，行为可控，也方便出问题时直接翻看。
    """
    base = _WORKSPACE / ".pytest-tmp"
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in request.node.nodeid)
    target = base / f"{next(_COUNTER):03d}-{safe_name[:60]}"
    target.mkdir(parents=True, exist_ok=True)
    return target
