"""Tkinter 界面的冒烟测试（无显示环境就跳过）。

只验证"能建出来、能列出已装版本、能算出基底原版版本"，
不点按钮、不真的下载——那些逻辑都在 core 里，已有单测覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

from gelauncher.config import Config  # noqa: E402
from gelauncher.core.layout import GameLayout  # noqa: E402
from gelauncher.ui.app import LauncherApp  # noqa: E402

from conftest import FIXTURES  # noqa: E402


def _write_version(layout: GameLayout, version_id: str, payload: dict) -> None:
    path = layout.version_json_path(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("当前环境没有可用的窗口系统")
    root.withdraw()

    # 测试里绝不允许弹真窗口：把这些对话框换成记录器
    from gelauncher.ui import app as ui_app

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(ui_app.messagebox, "showerror", lambda title, text: shown.append((title, str(text))))
    monkeypatch.setattr(ui_app.messagebox, "showinfo", lambda title, text: shown.append((title, str(text))))
    monkeypatch.setattr(ui_app.messagebox, "showwarning", lambda title, text: shown.append((title, str(text))))
    monkeypatch.setattr(ui_app.messagebox, "askyesno", lambda title, text: True)

    layout = GameLayout(tmp_path / ".minecraft")
    vanilla = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))
    _write_version(layout, "1.21.8", vanilla)
    _write_version(
        layout,
        "fabric-loader-0.20.1-1.21.8",
        {"id": "fabric-loader-0.20.1-1.21.8", "inheritsFrom": "1.21.8", "mainClass": "x"},
    )
    config = Config(game_dir=str(layout.root))
    application = LauncherApp(root, config)
    application.dialogs = shown  # type: ignore[attr-defined]
    root.update()
    try:
        yield application
    finally:
        root.destroy()


def test_lists_installed_versions(app):
    rows = app.tree.get_children()
    assert set(rows) == {"1.21.8", "fabric-loader-0.20.1-1.21.8"}


def test_base_mc_version_follows_inherits(app):
    assert app.base_mc_version("fabric-loader-0.20.1-1.21.8") == "1.21.8"
    assert app.base_mc_version("1.21.8") == "1.21.8"


def test_print_command_reports_missing_files_without_crashing(app):
    """fixture 里没有真实的 libraries，所以这里走"报错"分支：
    既要把错误写进日志，也不能把异常抛到界面上。"""
    app.tree.selection_set("1.21.8")
    app.print_command()
    content = app.log.get("1.0", "end")
    assert "库文件" in content
    assert app.dialogs, "应该弹过一次错误框（测试里已被记录器替换）"
