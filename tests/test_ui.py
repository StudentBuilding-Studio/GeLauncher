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
    assert versions.info_vars["版本"].get() == "1.21.8"

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


def test_accounts_page_add_offline_and_select(app, monkeypatch):
    import tkinter.simpledialog

    accounts = app.pages["accounts"]
    app.show_page("accounts")
    assert "offline|Player" in accounts.tree.get_children()

    monkeypatch.setattr(tkinter.simpledialog, "askstring", lambda *a, **k: "Steve")
    accounts.add_offline()

    assert "Steve" in app.config.accounts
    assert app.config.selected_account == "Steve"
    assert app.config.selected_account_type == "offline"
    row_text = accounts.tree.item("offline|Steve", "text")
    assert row_text.startswith("●")

    accounts.tree.selection_set("offline|Player")
    accounts.use_selected()
    assert app.config.selected_account == "Player"
    assert accounts.tree.item("offline|Player", "text").startswith("●")

    accounts.tree.selection_set("offline|Steve")
    accounts.remove_selected()
    assert "Steve" not in app.config.accounts
    assert app.config.selected_account in app.config.accounts


def test_accounts_page_add_microsoft(app, monkeypatch):
    from gelauncher.ui import msa_login

    record = {
        "type": "msa",
        "name": "MSPlayer",
        "uuid": "uuid-ms",
        "refresh_token": "R",
        "access_token": "A",
        "expires_at": 0,
    }
    monkeypatch.setattr(
        msa_login, "login_microsoft", lambda master, client_id, authority="consumers": record
    )

    accounts = app.pages["accounts"]
    app.show_page("accounts")
    accounts.add_microsoft()

    assert app.config.selected_account == "MSPlayer"
    assert app.config.selected_account_type == "msa"
    assert app.config.msa_account("MSPlayer") is not None
    assert "msa|MSPlayer" in accounts.tree.get_children()
    assert accounts.tree.item("msa|MSPlayer", "text").startswith("●")
    assert accounts.tree.item("msa|MSPlayer", "values")[0] == "微软"


def test_account_entries_list_both_kinds(app):
    app.config.add_msa_account({"name": "MSPlayer", "uuid": "u", "refresh_token": "r"})
    entries = app.config.account_entries()
    assert {entry["kind"] for entry in entries} == {"offline", "msa"}


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


def test_attach_fabric_uses_current_game_dir(app, monkeypatch):
    """装 Fabric 必须装到"当前目录"，而且版本 id 不变（装进现有版本）。"""
    import time

    from gelauncher.core import install_loader as core_loader

    calls = {}

    class FakeReport:
        version_id = "1.21.8"

    def fake_attach(layout, base, progress=None, **kwargs):
        calls["root"] = str(layout.root)
        calls["base"] = base
        return FakeReport()

    monkeypatch.setattr(core_loader, "attach_fabric", fake_attach)

    app.install_loader("1.21.8", "fabric")
    deadline = time.time() + 5
    while time.time() < deadline and "root" not in calls:
        time.sleep(0.02)
    assert calls["root"] == str(app.layout.root)
    assert calls["base"] == "1.21.8"


def test_install_loader_logs_target_dir(app, monkeypatch):
    import time

    from gelauncher.core import install_loader as core_loader

    class FakeReport:
        version_id = "1.21.8"

    monkeypatch.setattr(core_loader, "attach_fabric", lambda *a, **k: FakeReport())
    app.install_loader("1.21.8", "fabric")

    window = app.log_windows["1.21.8"]
    deadline = time.time() + 5
    content = ""
    while time.time() < deadline:
        try:
            content = window.text.get("1.0", "end")
        except tkinter.TclError:
            break
        if "装好了" in content:
            break
        app._poll()
        time.sleep(0.02)
    assert "安装目标目录" in content
    assert str(app.layout.root) in content


def test_log_window_writes_to_disk(app, tmp_path: Path):
    from gelauncher.ui.log_window import LogWindow

    path = tmp_path / "logs" / "install.log"
    window = LogWindow(app.root, "demo", log_path=path)
    window.append("[LAUNCHER] hello")
    window.append("[ERROR] boom")
    window.destroy()
    text = path.read_text(encoding="utf-8")
    assert "hello" in text and "boom" in text


def test_detail_page_disables_fabric_button_when_already_attached(app):
    """Fabric 已经装进这个版本时，安装按钮禁用、卸载按钮可用。"""
    import json

    versions = app.pages["versions"]
    versions.open("1.21.8")
    assert str(versions.fabric_button.cget("state")) == "normal"
    assert str(versions.uninstall_loader_button.cget("state")) == "disabled"

    # 模拟"已装 Fabric"：直接改版本 JSON 的 mainClass
    path = app.layout.version_json_path("1.21.8")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mainClass"] = "net.fabricmc.loader.impl.launch.knot.KnotClient"
    path.write_text(json.dumps(data), encoding="utf-8")

    versions.open("1.21.8")
    assert str(versions.fabric_button.cget("state")) == "disabled"
    assert str(versions.fabric_button.cget("text")) == "已装 Fabric"
    assert str(versions.uninstall_loader_button.cget("state")) == "normal"


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

# --------------------------------------------------------------------------- #
# 这轮 UI 改动：即时保存、删除版本、选版本安装
# --------------------------------------------------------------------------- #


def _pump(root, seconds: float = 2.0, predicate=None) -> None:
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        if predicate is not None and predicate():
            return
        time.sleep(0.02)


def test_delete_version_removes_directory_and_returns_to_list(app):
    versions = app.pages["versions"]
    versions.open("1.21.8")
    target = app.layout.version_dir("1.21.8")
    assert target.is_dir()

    app.delete_version("1.21.8")
    _pump(app.root, 1.0)

    assert not target.exists()
    assert versions.current is None
    assert "1.21.8" not in versions.tree.get_children()


def test_delete_refuses_running_version(app, monkeypatch):
    monkeypatch.setattr(app, "is_running", lambda version_id: True)
    versions = app.pages["versions"]
    versions.open("1.21.8")
    target = app.layout.version_dir("1.21.8")

    app.delete_version("1.21.8")
    _pump(app.root, 0.5)
    assert target.is_dir(), "正在运行的版本不能删"


def test_settings_save_instantly_without_button(app):
    settings = app.pages["settings"]
    app.show_page("settings")
    settings.memory_var.set("4096")
    settings.save()
    assert app.config.max_memory_mb == 4096

    settings.log_window_var.set(False)
    settings.save()
    assert app.config.show_log_window is False

    # 输入非法内存时不覆盖旧值，也不弹报错
    settings.memory_var.set("abc")
    settings.save()
    assert app.config.max_memory_mb == 4096


def test_install_dialog_lists_and_filters_versions(app, monkeypatch):
    from gelauncher.core.install_vanilla import RemoteVersion
    from gelauncher.ui import install_dialog

    fake = [
        RemoteVersion(id="1.21.8", type="release", url="u", release_time="2025-07-01T00:00:00Z"),
        RemoteVersion(id="1.21.9", type="release", url="u", release_time="2025-08-01T00:00:00Z"),
        RemoteVersion(
            id="26.4-snapshot-1", type="snapshot", url="u", release_time="2025-09-01T00:00:00Z"
        ),
    ]
    monkeypatch.setattr(
        "gelauncher.core.install_vanilla.remote_versions", lambda fetcher: list(fake)
    )

    chosen: list[tuple[str, str]] = []
    def _choose(version_id, instance):
        chosen.append((version_id, instance))
        return None

    dialog = install_dialog.VanillaInstallDialog(app.root, app, _choose)
    _pump(app.root, 3.0, lambda: dialog.ready)

    assert set(dialog.tree.get_children()) == {"1.21.8", "1.21.9"}

    dialog.mode_var.set("快照")
    dialog.refresh_rows()
    assert dialog.tree.get_children() == ("26.4-snapshot-1",)

    dialog.mode_var.set("全部")
    dialog.filter_var.set("1.21.9")
    dialog.refresh_rows()
    assert dialog.tree.get_children() == ("1.21.9",)

    dialog.tree.selection_set("1.21.9")
    dialog.install_selected()
    assert chosen == [("1.21.9", "1.21.9")]

# --------------------------------------------------------------------------- #
# 实例 / 加载器版本选择
# --------------------------------------------------------------------------- #


def test_install_vanilla_blocks_duplicate_instance(app, monkeypatch):
    from gelauncher.ui import app as ui_app

    called = {}

    class FakeReport:
        version_id = "新实例"

        class downloads:
            failures = []
            downloaded = 0

        messages = []

    def fake_install(layout, version_id, *, instance_name=None, progress=None, fetcher=None, cancel=None):
        called["instance"] = instance_name
        return FakeReport()

    monkeypatch.setattr(ui_app, "install_version", fake_install)

    # 1.21.8 这个实例已经存在：直接返回错误，不启动任务
    error = app.install_vanilla("1.21.8")
    assert error and "已经存在" in error
    assert "instance" not in called

    # 换个实例名才真的开装
    assert app.install_vanilla("1.21.8", "新实例") is None
    _pump(app.root, 5.0, lambda: "instance" in called)
    assert called["instance"] == "新实例"


def test_version_list_shows_instance_and_version(app):
    versions = app.pages["versions"]
    versions.refresh()
    values = versions.tree.item("1.21.8", "values")
    assert values[0] == "1.21.8", "版本列应该显示真实游戏版本"


def test_loader_dialog_lists_and_picks_version(app, monkeypatch):
    from gelauncher.core import install_loader as core
    from gelauncher.ui.loader_dialog import LoaderInstallDialog

    monkeypatch.setattr(
        core, "fabric_loader_versions", lambda fetcher, mc: ["0.99.0", "0.99.1"]
    )
    chosen: list[tuple[str, str]] = []
    dialog = LoaderInstallDialog(
        app.root, app, "1.21.8", "1.21.8", "fabric", lambda instance, kind, version: chosen.append((instance, kind, version))
    )
    _pump(app.root, 3.0, lambda: dialog.ready)

    assert dialog.tree.get_children() == ("0.99.1", "0.99.0"), "最新的排前面"
    dialog.install_selected()
    assert chosen == [("1.21.8", "fabric", "0.99.1")]

def test_install_dialog_instance_name_follows_selection(app, monkeypatch):
    from gelauncher.core.install_vanilla import RemoteVersion
    from gelauncher.ui import install_dialog

    fake = [
        RemoteVersion(id="1.21.8", type="release", url="u", release_time=""),
        RemoteVersion(id="1.21.9", type="release", url="u", release_time=""),
    ]
    monkeypatch.setattr(
        "gelauncher.core.install_vanilla.remote_versions", lambda fetcher: list(fake)
    )
    dialog = install_dialog.VanillaInstallDialog(app.root, app, lambda v, i: None)
    _pump(app.root, 3.0, lambda: dialog.ready)

    assert dialog.instance_var.get() == "1.21.8"

    # 选中另一个版本，实例名跟着变
    dialog.tree.selection_set("1.21.9")
    app.root.update()
    assert dialog.instance_var.get() == "1.21.9"

    # 用户手动改过之后就不再自动覆盖
    dialog.instance_var.set("我的世界")
    dialog._mark_edited()
    dialog.tree.selection_set("1.21.8")
    app.root.update()
    assert dialog.instance_var.get() == "我的世界"

    # 清空后重新跟随
    dialog.instance_var.set("")
    dialog._mark_edited()
    dialog.tree.selection_set("1.21.9")
    app.root.update()
    assert dialog.instance_var.get() == "1.21.9"

def test_modpack_import_dialog_defaults(app, tmp_path):
    from gelauncher.core.modpack import ModpackInfo
    from gelauncher.ui.import_dialog import ModpackImportDialog

    info = ModpackInfo(
        provider="Modrinth",
        name="我的整合包",
        version="1.0",
        game_version="1.20.1",
        loader="fabric",
        loader_version="0.15.11",
        overrides=["overrides/"],
    )
    dialog = ModpackImportDialog(app.root, app, tmp_path / "x.mrpack", info, lambda *a: None)
    assert dialog.instance_var.get() == "我的整合包"
    assert dialog.version_var.get() == "1.20.1"
    assert dialog.loader_var.get() == "fabric 0.15.11"
    dialog.destroy()

def test_task_window_has_cancel_button(app):
    token, window = app.begin_task("测试实例")
    assert window.cancel_button is not None
    assert str(window.cancel_button.cget("text")) == "取消"
    window._do_cancel()
    assert token.cancelled()
    window.destroy()
    app.task_window = None
    app.task_token = None


def test_cancelled_install_cleans_partial_and_reports(app, monkeypatch):
    from gelauncher.core.cancel import Cancelled
    from gelauncher.ui import app as ui_app

    def fake_install(layout, version_id, *, instance_name=None, progress=None, fetcher=None, cancel=None):
        root = layout.version_dir(instance_name)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{instance_name}.json").write_text("{}", encoding="utf-8")
        raise Cancelled()

    monkeypatch.setattr(ui_app, "install_version", fake_install)
    assert app.install_vanilla("1.21.8", "取消实例") is None
    _pump(app.root, 5.0, lambda: not app.busy)
    assert not app.layout.version_dir("取消实例").exists()
    assert any("已取消" in text for _kind, text in app.dialogs), app.dialogs

def test_finished_install_closes_task_window_and_shows_result(app, monkeypatch):
    from gelauncher.ui import app as ui_app

    class FakeReport:
        version_id = "完成实例"
        messages = []
        class downloads:
            failures = []
            downloaded = 3

    def fake_install(layout, version_id, *, instance_name=None, progress=None, fetcher=None, cancel=None):
        return FakeReport()

    monkeypatch.setattr(ui_app, "install_version", fake_install)
    assert app.install_vanilla("1.21.8", "完成实例") is None
    _pump(app.root, 5.0, lambda: not app.busy and app.task_window is None)
    assert app.task_window is None
    assert any(kind == "info" and "安装完成" in text for kind, _title, text in [
        (k, t, m) for k, t, m in [(d[0], d[0], d[1]) for d in app.dialogs]
    ]) or any("安装完成" in str(d[1]) for d in app.dialogs)
