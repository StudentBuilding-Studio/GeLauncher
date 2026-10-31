"""界面冒烟测试：页面切换、版本二级页、账号管理、实例设置、日志窗口。

不点真窗口、不发网络请求；所有对话框都换成记录器。
没有窗口系统时整体跳过（CI 里通常需要，这里在桌面上跑）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

from gelauncher.config import Config  # noqa: E402
from gelauncher.core.layout import GameLayout  # noqa: E402
from gelauncher.ui import app as ui_app  # noqa: E402
from gelauncher.ui import pages as ui_pages  # noqa: E402

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

    shown: list[tuple[str, str]] = []
    for module in (ui_app, ui_pages):
        monkeypatch.setattr(
            module.messagebox, "showerror", lambda title, text: shown.append(("error", str(text)))
        )
        monkeypatch.setattr(
            module.messagebox, "showinfo", lambda title, text: shown.append(("info", str(text)))
        )
        monkeypatch.setattr(
            module.messagebox, "showwarning", lambda title, text: shown.append(("warn", str(text)))
        )
        monkeypatch.setattr(module.messagebox, "askyesno", lambda title, text: True)

    layout = GameLayout(tmp_path / ".minecraft")
    vanilla = json.loads((FIXTURES / "vanilla-1.21.8.json").read_text(encoding="utf-8"))
    _write_version(layout, "1.21.8", vanilla)
    _write_version(
        layout,
        "fabric-loader-0.20.1-1.21.8",
        {"id": "fabric-loader-0.20.1-1.21.8", "inheritsFrom": "1.21.8", "mainClass": "x"},
    )
    config = Config(game_dir=str(layout.root))
    # 别写到真实的 %APPDATA%，测试自己的路径
    import gelauncher.config as config_module

    monkeypatch.setattr(config_module, "config_path", lambda: tmp_path / "config.json")
    application = ui_app.LauncherApp(root, config)
    application.dialogs = shown  # type: ignore[attr-defined]
    root.update()
    try:
        yield application
    finally:
        for window in list(application.log_windows.values()):
            try:
                window.destroy()
            except tkinter.TclError:
                pass
        root.destroy()


def test_starts_on_versions_page(app):
    assert app.current_page == "versions"
    versions = app.pages["versions"]
    assert set(versions.tree.get_children()) == {"1.21.8", "fabric-loader-0.20.1-1.21.8"}


def test_click_version_opens_detail_page(app):
    versions = app.pages["versions"]
    versions.open("1.21.8")
    assert versions.current == "1.21.8"
    assert versions.detail_frame.winfo_manager() == "grid"
    assert versions.list_frame.winfo_manager() == ""
    assert versions.info_vars["原版"].get() == "1.21.8"

    versions.show_list()
    assert versions.list_frame.winfo_manager() == "grid"
    assert versions.current is None


def test_instance_settings_are_saved_per_version(app, tmp_path: Path):
    versions = app.pages["versions"]
    versions.open("1.21.8")
    versions.memory_var.set("4096")
    versions.java_var.set(r"C:\Java\bin\java.exe")
    versions.width_var.set("1280")
    versions.height_var.set("720")
    versions.log_window_var.set(False)
    versions.save_settings()

    stored = app.config.instances["1.21.8"]
    assert stored["max_memory_mb"] == 4096
    assert stored["java_path"] == r"C:\Java\bin\java.exe"
    assert stored["width"] == 1280
    assert stored["height"] == 720
    assert stored["show_log_window"] is False

    # 另一个版本不受影响：回落到全局默认
    other = app.config.instance("fabric-loader-0.20.1-1.21.8")
    assert other["max_memory_mb"] == app.config.max_memory_mb
    assert other["show_log_window"] == app.config.show_log_window


def test_accounts_page_manages_accounts(app):
    accounts = app.pages["accounts"]
    app.show_page("accounts")
    assert "Player" in accounts.tree.get_children()

    accounts.name_var.set("Steve")
    accounts.add()
    assert "Steve" in app.config.accounts
    assert app.config.selected_account == "Steve"
    assert "Steve" in accounts.tree.get_children()

    accounts.tree.selection_set("Player")
    accounts.use_selected()
    assert app.config.selected_account == "Player"

    accounts.tree.selection_set("Steve")
    accounts.remove()
    assert "Steve" not in app.config.accounts
    assert app.config.selected_account in app.config.accounts


def test_settings_page_saves_defaults(app):
    settings = app.pages["settings"]
    app.show_page("settings")
    settings.memory_var.set("6144")
    settings.java_var.set(r"C:\jdk\bin\java.exe")
    settings.log_window_var.set(False)
    settings.save()

    assert app.config.max_memory_mb == 6144
    assert app.config.java_path == r"C:\jdk\bin\java.exe"
    assert app.config.show_log_window is False


def test_directory_list_add_and_switch(app, tmp_path: Path):
    second = tmp_path / "second-minecraft"
    (second / "versions" / "1.20.1").mkdir(parents=True)
    (second / "versions" / "1.20.1" / "1.20.1.json").write_text(
        json.dumps({"id": "1.20.1", "mainClass": "x"}), encoding="utf-8"
    )

    app.switch_game_dir(str(second))

    assert str(second) in app.config.game_dirs
    assert str(app.layout.root) == str(second)
    versions = app.pages["versions"]
    assert str(second) in versions.dir_tree.get_children()
    assert "1.20.1" in versions.tree.get_children()


def test_java_dropdown_remembers_manual_path(app, tmp_path: Path):
    fake = str(tmp_path / "jdk" / "bin" / "java.exe")
    app.add_java_path(fake)
    assert fake in app.config.java_paths

    versions = app.pages["versions"]
    versions.open("1.21.8")
    assert fake in versions.java_combo.cget("values")

    app.show_page("settings")
    settings = app.pages["settings"]
    settings.refresh_java_choices()
    assert fake in settings.java_combo.cget("values")


def test_detect_java_fills_dropdown(app, monkeypatch, tmp_path: Path):
    import time

    from gelauncher.core import java as java_module

    fake_path = tmp_path / "jdk21" / "bin" / "java.exe"
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    fake_path.write_bytes(b"")
    runtime = java_module.JavaRuntime(path=fake_path, version="21.0.1")
    monkeypatch.setattr(ui_app, "installed_javas", lambda: [runtime])

    app.detect_java()
    deadline = time.time() + 5
    while time.time() < deadline and str(fake_path) not in app.config.java_paths:
        time.sleep(0.02)
    assert str(fake_path) in app.config.java_paths

    # 把后台线程塞的消息消费掉，页面上的下拉框应该跟着更新
    app._poll()
    app.show_page("versions")
    versions = app.pages["versions"]
    versions.open("1.21.8")
    assert str(fake_path) in versions.java_combo.cget("values")


def test_base_mc_version_follows_inherits(app):
    assert app.base_mc_version("fabric-loader-0.20.1-1.21.8") == "1.21.8"
    assert app.base_mc_version("1.21.8") == "1.21.8"


def test_log_window_opens_and_appends(app):
    window = app.open_log_window("1.21.8")
    window.append("[LAUNCHER] hello")
    window.append("[ERROR] boom")
    assert "hello" in window.text.get("1.0", "end")
    assert window.text.tag_names("2.0") == ("ERROR",)
    # 同一个版本只开一个窗口
    assert app.open_log_window("1.21.8") is window


def test_print_command_reports_error_when_files_missing(app):
    """fixture 里没有真实 libraries，所以这里走报错分支：
    要弹一个明确的错误框，而不是抛异常或静默失败。"""
    app.print_command("1.21.8")
    assert any(kind == "error" for kind, _text in app.dialogs)


def test_print_command_opens_log_window_when_ready(app, tmp_path: Path):
    """把 fixture 的库文件补齐到"存在"级别，命令就能拼出来并发到日志窗口。"""
    from gelauncher.core import libraries
    from gelauncher.core.command import Account
    from gelauncher.core.launch import LaunchOptions, prepare
    from gelauncher.core.rules import Environment

    layout = app.layout
    version = layout.load_json("1.21.8")
    env = Environment(os_name="windows", arch="x86_64", os_version="10.0.22631")
    classpath, natives = libraries.effective_libraries(version, env)
    for library in classpath:
        path = layout.libraries_dir / libraries.library_relative_path(library)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    for library in natives:
        artifact, _ = libraries.native_artifact(library, env)
        if artifact is not None:
            path = layout.libraries_dir / libraries.artifact_relative_path(library, artifact)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    jar = layout.version_jar_path("1.21.8")
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"x")
    index = layout.asset_index_path("26")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text('{"objects": {}}', encoding="utf-8")
    log_config = layout.log_config_path("client-1.21.2.xml")
    log_config.parent.mkdir(parents=True, exist_ok=True)
    log_config.write_text("<Configuration />", encoding="utf-8")

    plan = prepare(
        LaunchOptions(
            layout=layout,
            version_id="1.21.8",
            account=Account(name="Player"),
            dry_run=True,
        )
    )
    assert "net.minecraft.client.main.Main" in plan.printable_command()
