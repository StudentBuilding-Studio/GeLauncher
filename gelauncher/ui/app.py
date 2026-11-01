"""主窗口：左侧一级导航（版本 / 账号 / 设置）+ 内容区 + 状态栏。

相比上一版的变化（按用户要求）：
- 版本列表单击进入二级页，二级页里放该版本自己的设置（参考 HMCL）；
- 主窗口不再有常驻日志区，改成"每个游戏进程一个独立日志窗口"，
  是否自动打开由该版本的设置决定；
- 玩家名从版本设置里挪走，单独做成"账号"页统一管理。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from gelauncher import __version__
from gelauncher.config import Config
from gelauncher.core import microsoft
from gelauncher.core.command import Account
from gelauncher.core.install_loader import install_forge  # noqa: F401  （install_forge 仍被旧代码引用）
from gelauncher.core.install_vanilla import install_version
from gelauncher.core.java import installed_javas
from gelauncher.core.launch import LaunchOptions, prepare, run
from gelauncher.core.layout import GameLayout
from gelauncher.ui.log_window import LogWindow
from gelauncher.ui.pages import AccountsPage, SettingsPage, VersionsPage

NAV_ITEMS = (
    ("versions", "版本", "📦"),
    ("accounts", "账号", "👤"),
    ("settings", "设置", "⚙"),
)


class LauncherApp:
    def __init__(self, root: tk.Tk, config: Config) -> None:
        self.root = root
        self.config = config
        # 保证当前目录一定在"目录列表"里（第一次用 --game-dir 或配置里缺它时）
        self.config.add_game_dir(str(config.game_dir_path()))
        self.layout = GameLayout(config.game_dir_path())
        self.messages: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.processes: dict[str, object] = {}
        self.log_windows: dict[str, LogWindow] = {}
        self.task_token = None
        self.task_window: LogWindow | None = None
        self.crash_log = self.layout.root / "logs" / "gelauncher-ui-crash.log"
        self.nav_buttons: dict[str, ttk.Button] = {}

        self.status_var = tk.StringVar(value="就绪")
        self.root.report_callback_exception = self._on_callback_exception
        from gelauncher.ui import theme
        from gelauncher.ui.dpi import apply_tk_scaling, system_dpi

        theme.set_initial(config.appearance)
        if theme.is_ctk(self.root):
            self.scale = system_dpi() / 96.0
        else:
            self.scale = apply_tk_scaling(self.root)

        self._setup_fonts()
        self._setup_style()
        self._build()
        self._restore_geometry()
        self.show_page("versions")
        self.root.after(300, self._preload)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll)

    # ---------------------------------------------------------------- 外观

    def _setup_fonts(self) -> None:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(size=10, family="Microsoft YaHei UI")
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(size=9)
        except tk.TclError:
            pass

    def _setup_style(self) -> None:
        from gelauncher.ui import theme

        theme.style_ttk(self.root)
        ttk.Style().configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))

    # ---------------------------------------------------------------- 布局

    def _build(self) -> None:
        self.root.title(f"Ge 启动器 {__version__}")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        from gelauncher.ui import theme

        scale = getattr(self, "scale", 1.0)
        if theme.is_ctk(self.root):
            self.root.minsize(920, 560)
        else:
            self.root.minsize(int(920 * scale), int(560 * scale))
        if theme.available():
            import customtkinter as ctk

            sidebar = ctk.CTkFrame(self.root, corner_radius=0, width=150)
            sidebar.grid(row=0, column=0, sticky="ns")
            sidebar.grid_propagate(False)
            sidebar.columnconfigure(0, weight=1)
            ctk.CTkLabel(
                sidebar,
                text="Ge 启动器",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=14, weight="bold"),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=10, pady=(14, 12))
            for index, (key, label, icon) in enumerate(NAV_ITEMS, start=1):
                button = ctk.CTkButton(
                    sidebar,
                    text=f"{icon}  {label}",
                    anchor="w",
                    height=36,
                    corner_radius=8,
                    fg_color="transparent",
                    text_color=theme.TEXT,
                    hover_color=("#e2e8f0", "#2a2f37"),
                    command=lambda name=key: self.show_page(name),
                )
                button.grid(row=index, column=0, sticky="ew", padx=8, pady=2)
                self.nav_buttons[key] = button
            sidebar.rowconfigure(len(NAV_ITEMS) + 1, weight=1)
            self.status_label = ctk.CTkLabel(
                sidebar,
                textvariable=self.status_var,
                text_color=theme.MUTED,
                wraplength=130,
                justify="left",
                anchor="sw",
            )
            self.status_label.grid(
                row=len(NAV_ITEMS) + 2, column=0, sticky="ew", padx=10, pady=(0, 10)
            )
            self.content = ctk.CTkFrame(self.root, corner_radius=0, fg_color=theme.BG)
        else:
            sidebar = ttk.Frame(self.root, padding=(10, 12, 6, 8), width=150)
            sidebar.grid(row=0, column=0, sticky="ns")
            sidebar.grid_propagate(False)
            sidebar.columnconfigure(0, weight=1)
            ttk.Label(sidebar, text="Ge 启动器", font=("Microsoft YaHei UI", 12, "bold")).grid(
                row=0, column=0, sticky="w", pady=(0, 14)
            )
            for index, (key, label, icon) in enumerate(NAV_ITEMS, start=1):
                button = ttk.Button(
                    sidebar,
                    text=f"{icon}  {label}",
                    style="Nav.TButton",
                    command=lambda name=key: self.show_page(name),
                )
                button.grid(row=index, column=0, sticky="ew", pady=2)
                self.nav_buttons[key] = button
            sidebar.rowconfigure(len(NAV_ITEMS) + 1, weight=1)
            ttk.Label(sidebar, textvariable=self.status_var, foreground="#666", wraplength=130).grid(
                row=len(NAV_ITEMS) + 2, column=0, sticky="sw"
            )
            self.content = ttk.Frame(self.root)

        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.pages = {
            "versions": VersionsPage(self.content, self),
            "accounts": AccountsPage(self.content, self),
            "settings": SettingsPage(self.content, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

        self.current_page = ""

    def _preload(self) -> None:
        """启动后预热首次点击会用到的东西：Java 探测、jar 内嵌版本号、实例 JSON。"""
        import threading

        paths = list(self.java_choices())
        layout = self.layout

        def work() -> None:
            from gelauncher.core.java import probe_java_cached

            for path in paths:
                try:
                    probe_java_cached(path)
                except Exception:  # noqa: BLE001
                    pass
            try:
                versions = list(layout.installed_versions())
            except OSError:
                return
            for version_id in versions:
                try:
                    layout.load_json(version_id)
                    layout.actual_version(version_id)  # 读 jar 里的 version.json（带缓存）
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _restore_geometry(self) -> None:
        """默认窗口大小按屏幕逻辑分辨率算；CTk 的 geometry/minsize 会自动乘缩放。"""
        from gelauncher.ui import theme

        geometry = self.config.window_geometry or ""
        scale = getattr(self, "scale", 1.0)
        ctk_root = theme.is_ctk(self.root)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # CTk 的 geometry() 接受逻辑像素；普通 Tk 是物理像素
        view_w = screen_w / scale if ctk_root else screen_w
        view_h = screen_h / scale if ctk_root else screen_h
        default_w = min(max(int(view_w * 0.72), 900), 1280)
        default_h = min(max(int(view_h * 0.78), 600), 820)

        def _apply(width: int, height: int) -> None:
            if ctk_root:
                self.root.geometry(f"{width}x{height}")
            else:
                self.root.geometry(f"{int(width * scale)}x{int(height * scale)}")

        if geometry:
            import re

            match = re.match(r"(\d+)x(\d+)", geometry)
            fits = True
            if match:
                saved_w, saved_h = int(match.group(1)), int(match.group(2))
                fits = saved_w <= view_w and saved_h <= view_h - 40
            if fits:
                try:
                    self.root.geometry(geometry)
                    return
                except tk.TclError:
                    pass
        _apply(default_w, default_h)

    # ---------------------------------------------------------------- 导航

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            return
        from gelauncher.ui import theme

        for key, page in self.pages.items():
            active = key == name
            if active:
                # 先更新内容再显示：否则窗口会先闪一下旧列表再刷新
                page.on_show()
                page.grid()
            else:
                page.grid_remove()
            button = self.nav_buttons.get(key)
            if button is None:
                continue
            if type(button).__name__ == "CTkButton":
                button.configure(
                    fg_color=theme.ACCENT if active else "transparent",
                    text_color="#ffffff" if active else theme.TEXT,
                )
            else:
                button.state(["pressed"] if active else ["!pressed"])
        self.current_page = name

    def page(self, name: str):
        return self.pages[name]

    # ---------------------------------------------------------------- 状态 / 通知

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def notify(self, text: str) -> None:
        """没有常驻日志区了：提示统一进状态栏 + 已打开的日志窗口。"""
        self.set_status(text)
        for window in self.log_windows.values():
            try:
                window.append(f"[LAUNCHER] {text}")
            except tk.TclError:
                pass

    def log_line(self, version_id: str, text: str) -> None:
        window = self.log_windows.get(version_id)
        if window is not None:
            try:
                window.append(text)
            except tk.TclError:
                self.log_windows.pop(version_id, None)

    # ---------------------------------------------------------------- 日志窗口

    def open_log_window(
        self, version_id: str, *, task: bool = False, on_cancel=None
    ) -> LogWindow:
        existing = self.log_windows.get(version_id)
        if existing is not None and not task:
            try:
                existing.deiconify()
                existing.lift()
                return existing
            except tk.TclError:
                self.log_windows.pop(version_id, None)
        if existing is not None and task:
            # 安装/导入要用带取消按钮的窗口，旧日志窗口先关掉
            self.log_windows.pop(version_id, None)
            try:
                existing.destroy()
            except tk.TclError:
                pass
        window = LogWindow(
            self.root,
            version_id,
            log_path=self.layout.root / "logs" / f"gelauncher-{version_id}.log",
            task=task,
            on_cancel=on_cancel,
        )

        def forget() -> None:
            self.log_windows.pop(version_id, None)
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", forget)
        self.log_windows[version_id] = window
        return window


    # ---------------------------------------------------------------- 任务窗口

    def begin_task(self, instance: str) -> tuple:
        """开一个带"取消"按钮的安装/导入窗口，返回 (cancel_token, window)。"""
        from gelauncher.core.cancel import CancelToken

        token = CancelToken()
        window = self.open_log_window(instance, task=True, on_cancel=token.cancel)
        self.task_token = token
        self.task_window = window
        return token, window

    def finish_task(self, level: str, message: str) -> None:
        """任务收尾：关掉安装窗口，弹结果框。level = ok / cancelled / error。"""
        window = self.task_window
        self.task_window = None
        self.task_token = None
        instance = getattr(window, "version_id", None)
        if instance:
            self.log_windows.pop(instance, None)
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass
        if level == "ok":
            messagebox.showinfo("完成", message)
        elif level == "cancelled":
            messagebox.showinfo("已取消", message)
        else:
            messagebox.showerror("失败", message)

    def cleanup_partial(self, instance: str, before_versions: set[str], created: bool) -> None:
        """取消/失败时清掉这次新建的实例目录和 installer 生成的临时版本目录。"""
        import shutil

        versions_root = self.layout.versions_dir.resolve()
        targets: list[Path] = []
        if created:
            targets.append(self.layout.version_dir(instance))
        for name in set(self.layout.installed_versions()) - before_versions:
            if name != instance:
                targets.append(self.layout.version_dir(name))
        for target in targets:
            try:
                resolved = target.resolve()
                if resolved.parent != versions_root or not resolved.is_dir():
                    continue
                shutil.rmtree(resolved)
                self.log_line(instance, f"[LAUNCHER] 已清理残留目录：{resolved.name}")
            except (OSError, RuntimeError):
                pass

    # ---------------------------------------------------------------- 游戏进程

    def is_running(self, version_id: str) -> bool:
        process = self.processes.get(version_id)
        return bool(process is not None and process.poll() is None)

    # ---------------------------------------------------------------- Java 自动安装

    def ensure_java(self, version_id: str) -> bool:
        """检查版本要求的 Java；缺了就提示自动下载。返回 False 表示先别启动。"""
        from gelauncher.core.java import choose_java, probe_java
        from gelauncher.core.models import VersionJsonError

        try:
            version = self.layout.load_json(version_id)
        except VersionJsonError:
            return True
        required = version.java_version
        if not required:
            return True

        settings = self.config.instance(version_id)
        manual = str(settings.get("java_path") or "").strip()
        if manual:
            runtime = probe_java(manual)
            if runtime is not None and runtime.major >= required:
                return True
            reason = (
                f"手动指定的 Java {runtime.major} 低于要求的 Java {required}"
                if runtime is not None
                else f"手动指定的 Java 跑不起来：{manual}"
            )
            if not messagebox.askyesno(
                "Java 不符合要求",
                f"{reason}。\n\n是否自动下载安装 Temurin JDK {required}？（约 190MB）",
            ):
                return True
            return self._start_java_install(required)

        runtime, satisfied = choose_java(
            required, self.java_choices(), preferred=self.config.java_path
        )
        if satisfied:
            return True
        found = []
        for item in self.java_choices():
            probed = probe_java(item)
            if probed is not None:
                found.append(f"Java {probed.major}")
        have = "、".join(sorted(set(found))) or "无"
        if not messagebox.askyesno(
            "缺少 Java",
            f"「{version_id}」需要 Java {required}，本机检测到：{have}。\n\n"
            f"是否自动下载安装 Temurin JDK {required}？（约 190MB）",
        ):
            messagebox.showwarning(
                "没有合适的 Java",
                f"继续启动可能会失败。可以稍后在设置里手动指定 Java {required}。",
            )
            return True
        return self._start_java_install(required)

    def _start_java_install(self, required: int) -> bool:
        """下载 Java（带取消按钮），装完提示用户重新启动。返回 False（先别启动游戏）。"""
        from gelauncher.core.java_install import install_java

        instance = f"Java {required}"
        token, window = self.begin_task(instance)
        window.start_session(f"安装 Temurin JDK {required}")
        window.append(f"[LAUNCHER] 安装位置：{self.layout.root / 'launcher' / 'java'}")
        window.set_status(f"下载 Java {required}…")
        fetcher = self.make_fetcher()

        def progress(stage: str, message: str) -> None:
            self.messages.put(("log", instance, f"[{stage}] {message}"))

        def work() -> None:
            from gelauncher.core.cancel import Cancelled

            try:
                runtime = install_java(
                    self.layout.root,
                    required,
                    fetcher=fetcher,
                    progress=progress,
                    cancel=token,
                )
                self.config.add_java_path(str(runtime.path))
                self.config.save()
                self.messages.put(
                    ("log", instance, f"[LAUNCHER] Java {runtime.major} 已加入候选：{runtime.path}")
                )
                self.messages.put(("refresh_java", None))
                self.messages.put(
                    ("task_done", ("ok", f"Java {runtime.major} 已安装，现在可以启动了"))
                )
            except Cancelled:
                self.messages.put(("log", instance, "[LAUNCHER] 已取消"))
                self.messages.put(("task_done", ("cancelled", f"已取消安装 Java {required}")))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("log", instance, f"[ERROR] Java 安装失败：{exc}"))
                self.messages.put(("task_done", ("error", f"Java {required} 安装失败：{exc}")))
            finally:
                self.messages.put(("idle", None))

        self._start_job(work)
        return False

    def launch(self, version_id: str) -> None:
        if self.busy:
            messagebox.showinfo("等一下", "上一个任务还没结束")
            return
        if self.is_running(version_id):
            messagebox.showinfo("已经在跑了", f"{version_id} 已在运行")
            return
        if not self.ensure_java(version_id):
            return

        settings = self.config.instance(version_id)
        window = self.open_log_window(version_id) if settings.get("show_log_window", True) else None
        if window is not None:
            window.start_session(f"启动 {version_id}")
            window.set_status("准备启动…")

        def build_options(account) -> LaunchOptions:
            return LaunchOptions(
                layout=self.layout,
                version_id=version_id,
                account=account,
                java_path=Path(settings["java_path"]) if settings.get("java_path") else None,
                max_memory_mb=int(settings["max_memory_mb"]),
                width=settings.get("width"),
                height=settings.get("height"),
            )

        def work() -> None:
            try:
                self.messages.put(("log", version_id, f"[LAUNCHER] 账号：{self.config.selected_account}"))
                account = self.designated_account()
                plan = prepare(build_options(account))
                for warning in plan.warnings:
                    self.messages.put(("log", version_id, f"[WARN] {warning}"))
                log_path = self.layout.root / "logs" / f"gelauncher-{version_id}.log"
                self.messages.put(("log", version_id, f"[LAUNCHER] 日志文件：{log_path}"))
                process = run(
                    plan,
                    log_path=log_path,
                    on_line=lambda line: self.messages.put(("log", version_id, line)),
                    wait=False,
                )
                self.processes[version_id] = process
                self.messages.put(("status", f"{version_id} 运行中（Java {plan.java.major}）"))
                self.messages.put(("running", version_id))
                if window is not None:
                    self.messages.put(("log_status", version_id, "游戏运行中…"))
            except Exception as exc:  # noqa: BLE001 - 启动失败要显示出来
                self.messages.put(("log", version_id, f"[ERROR] 启动失败：{exc}"))
                if window is not None:
                    self.messages.put(("log_status", version_id, "启动失败"))
                self.messages.put(("status", f"启动 {version_id} 失败"))
                self.messages.put(("error", f"启动失败：{exc}"))
                self.messages.put(("stopped", version_id))
                return

            # 等待游戏退出放到另一个线程：游戏跑着的时候界面不能被 busy 锁住
            def waiter() -> None:
                code = process.wait()
                self.messages.put(("log", version_id, f"[LAUNCHER] 游戏退出，退出码 {code}"))
                self.messages.put(("log_status", version_id, f"已退出（退出码 {code}）"))
                self.messages.put(("status", f"{version_id} 已退出（退出码 {code}）"))
                self.messages.put(("stopped", version_id))

            threading.Thread(target=waiter, daemon=True).start()

        self._start_job(work)

    def stop(self, version_id: str) -> None:
        process = self.processes.get(version_id)
        if process is not None and process.poll() is None:
            process.terminate()
            self.set_status(f"正在结束 {version_id}…")

    # ---------------------------------------------------------------- 安装 / 命令

    def base_mc_version(self, version_id: str) -> str:
        """实例对应的真实游戏版本（实例名可以和版本号不同）。"""
        return self.layout.actual_version(version_id)

    def designated_account(self) -> Account:
        """本次启动用的账号（微软账号会自动刷新 token）。"""
        from gelauncher.core.accounts import resolve_account

        return resolve_account(self.config)

    def print_command(self, version_id: str) -> None:
        settings = self.config.instance(version_id)
        try:
            account = self.designated_account()
        except Exception:  # noqa: BLE001 - 只是打印命令，别因为登录拦住
            account = Account.offline(self.config.selected_account or "Player")
        options = LaunchOptions(
            layout=self.layout,
            version_id=version_id,
            account=account,
            java_path=Path(settings["java_path"]) if settings.get("java_path") else None,
            max_memory_mb=int(settings["max_memory_mb"]),
            dry_run=True,
        )
        try:
            plan = prepare(options)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("拼不出命令", str(exc))
            return
        window = self.open_log_window(version_id)
        window.start_session(f"启动 {version_id}")
        window.append("=" * 70)
        window.append(plan.printable_command())
        window.append("=" * 70)
        window.set_status("已打印启动命令")

    def make_fetcher(self):
        """按设置里的「下载源」造下载器（auto / official / mirror）。"""
        from gelauncher.core.net import Fetcher, sources_for

        return Fetcher(sources=sources_for(self.config.download_source))

    def import_modpack(self, archive, info, instance: str, game_version: str) -> str | None:
        """导入整合包；返回错误文案（None 表示已经开装）。"""
        from gelauncher.core.install_vanilla import (
            InstallError,
            validate_instance_name,
        )

        try:
            instance = validate_instance_name(instance)
        except InstallError as exc:
            return str(exc)
        if self.layout.instance_exists(instance) or self.layout.version_dir(instance).exists():
            return f"实例「{instance}」已经存在，换个名字"

        token, window = self.begin_task(instance)
        window.start_session(f"导入整合包 {info.name}")
        window.append(f"[LAUNCHER] 游戏目录：{self.layout.root}")
        window.append(f"[LAUNCHER] 来源：{archive}")
        window.append(f"[LAUNCHER] {info.summary()}")
        window.set_status("导入整合包…")
        fetcher = self.make_fetcher()
        before_versions = set(self.layout.installed_versions())

        def progress(stage: str, message: str) -> None:
            self.messages.put(("log", instance, f"[{stage}] {message}"))

        def work() -> None:
            from gelauncher.core import modpack
            from gelauncher.core.cancel import Cancelled

            try:
                report = modpack.install_modpack(
                    self.layout,
                    Path(archive),
                    instance_name=instance,
                    info=info,
                    game_version=game_version,
                    curse_api_key=modpack.effective_curse_api_key(self.config.curse_api_key),
                    fetcher=fetcher,
                    progress=progress,
                    cancel=token,
                )
                self.messages.put(
                    (
                        "log",
                        instance,
                        f"[LAUNCHER] 导入完成：下载 {report.downloads.downloaded} 个文件，"
                        f"已存在 {report.downloads.skipped} 个",
                    )
                )
                for note in info.notes:
                    self.messages.put(("log", instance, f"[LAUNCHER] {note}"))
                self.messages.put(("status", f"{instance} 导入完成"))
                self.messages.put(("refresh", report.instance))
                self.messages.put(("open_version", report.instance))
                self.messages.put(("task_done", ("ok", f"整合包「{instance}」导入完成")))
            except Cancelled:
                self.cleanup_partial(instance, before_versions, created=True)
                self.messages.put(("log", instance, "[LAUNCHER] 已取消，清理残留文件"))
                self.messages.put(
                    ("task_done", ("cancelled", f"已取消导入「{instance}」，本次产生的文件已清理"))
                )
            except Exception as exc:  # noqa: BLE001
                self.cleanup_partial(instance, before_versions, created=True)
                self.messages.put(("log", instance, f"[ERROR] 导入失败：{exc}"))
                self.messages.put(("task_done", ("error", f"导入「{instance}」失败：{exc}")))
            finally:
                self.messages.put(("idle", None))

        self._start_job(work)
        return None

    def install_loader(self, instance: str, kind: str, loader_version: str | None = None) -> None:
        from gelauncher.core.install_loader import LOADER_LABELS, loader_of

        if kind not in LOADER_LABELS:
            messagebox.showerror("未知加载器", f"不认识的加载器：{kind}")
            return
        if not self.layout.instance_exists(instance):
            messagebox.showerror(
                "没有这个实例", f"实例「{instance}」不存在，先用「安装原版…」建一个。"
            )
            return
        name = LOADER_LABELS[kind]
        current = loader_of(self.layout, instance)
        if current == kind:
            return
        if current is not None:
            messagebox.showwarning(
                "已经有加载器", f"{instance} 现在装的是 {LOADER_LABELS[current]}，先卸载它。"
            )
            return
        token, log_window = self.begin_task(instance)
        log_window.start_session(f"给 {instance} 安装 {name}")
        log_window.append(f"[LAUNCHER] 安装目标目录：{self.layout.root}")
        log_window.append(f"[LAUNCHER] 下载源：{self.config.download_source}")
        log_window.set_status(f"安装 {name}…")
        fetcher = self.make_fetcher()
        before_versions = set(self.layout.installed_versions())

        def progress(stage: str, message: str) -> None:
            self.messages.put(("log", instance, f"[{stage}] {message}"))

        def work() -> None:
            from gelauncher.core import install_loader as loaders
            from gelauncher.core.cancel import Cancelled

            try:
                if kind == "fabric":
                    report = loaders.attach_fabric(
                        self.layout,
                        instance,
                        loader_version=loader_version,
                        progress=progress,
                        fetcher=fetcher,
                        cancel=token,
                    )
                elif kind == "neoforge":
                    report = loaders.install_neoforge(
                        self.layout,
                        instance,
                        neoforge_version=loader_version,
                        progress=progress,
                        fetcher=fetcher,
                        cancel=token,
                    )
                else:
                    report = loaders.install_forge(
                        self.layout,
                        instance,
                        forge_version=loader_version,
                        progress=progress,
                        fetcher=fetcher,
                        cancel=token,
                    )
                self.messages.put(("log", instance, f"[LAUNCHER] 装好了：{report.version_id}"))
                self.messages.put(
                    ("status", f"{name} 已装进 {report.version_id}（{self.layout.root}）")
                )
                self.messages.put(("refresh", report.version_id))
                self.messages.put(("open_version", report.version_id))
                self.messages.put(("task_done", ("ok", f"{name} 已装进「{instance}」")))
            except Cancelled:
                self.cleanup_partial(instance, before_versions, created=False)
                self.messages.put(("log", instance, "[LAUNCHER] 已取消，清理残留文件"))
                self.messages.put(
                    ("task_done", ("cancelled", f"已取消安装 {name}，本次产生的临时文件已清理"))
                )
            except Exception as exc:  # noqa: BLE001
                self.cleanup_partial(instance, before_versions, created=False)
                self.messages.put(("log", instance, f"[ERROR] {name} 安装失败：{exc}"))
                self.messages.put(("task_done", ("error", f"{name} 安装失败：{exc}")))
            finally:
                self.messages.put(("idle", None))

        self._start_job(work)

    def detach_loader(self, version_id: str) -> None:
        from gelauncher.core.install_loader import LOADER_LABELS, detach_loader, loader_of

        current = loader_of(self.layout, version_id)
        if current is None:
            return
        if not messagebox.askyesno(
            "卸载加载器", f"把 {version_id} 还原成原版（卸载 {LOADER_LABELS[current]}）？"
        ):
            return
        if detach_loader(self.layout, version_id):
            self.set_status(f"{version_id} 已还原成原版")
            self.messages.put(("refresh", version_id))
            self.messages.put(("open_version", version_id))
        else:
            messagebox.showinfo("没有备份", "这个版本没有加载器安装记录，可能不是 Ge 装的")

    # 兼容旧调用点
    def detach_fabric(self, version_id: str) -> None:
        self.detach_loader(version_id)

    def delete_version(self, version_id: str) -> None:
        """删除版本目录（只删 versions/<id>，不碰 libraries/assets）。"""
        import shutil

        if self.is_running(version_id):
            messagebox.showwarning("游戏还在运行", f"{version_id} 正在运行，先关掉游戏再删。")
            return
        target = self.layout.version_dir(version_id)
        if not target.is_dir():
            messagebox.showinfo("找不到版本目录", str(target))
            return
        from gelauncher.ui.confirm import ask_danger

        if not ask_danger(
            self.root,
            title="删除版本",
            message=(
                f"确定删除 {version_id}？\n\n会删掉整个版本目录：\n{target}\n\n"
                "存档在 versions/<id>/saves 里的话也会一起没，删之前先备份。"
            ),
            confirm_text="删除",
        ):
            return
        try:
            shutil.rmtree(target)
        except OSError as exc:
            messagebox.showerror("删除失败", str(exc))
            return
        self.set_status(f"已删除 {version_id}")
        page = self.pages.get("versions")
        if page is not None:
            page.show_list()
            page.refresh()

    def install_vanilla(self, version_id: str, instance_name: str | None = None) -> str | None:
        """装原版到指定实例；返回错误文案（None 表示已经开装）。"""
        from gelauncher.core.install_vanilla import (
            InstallError,
            validate_instance_name,
        )

        try:
            instance = validate_instance_name(instance_name or version_id)
        except InstallError as exc:
            return str(exc)
        if self.layout.instance_exists(instance) or self.layout.version_dir(instance).exists():
            return f"实例「{instance}」已经存在，换个名字"

        token, window = self.begin_task(instance)
        window.start_session(f"安装 {version_id} 到实例「{instance}」")
        window.append(f"[LAUNCHER] 安装目标目录：{self.layout.root}")
        window.append(f"[LAUNCHER] 下载源：{self.config.download_source}")
        window.set_status(f"安装 {version_id}…")
        fetcher = self.make_fetcher()
        before_versions = set(self.layout.installed_versions())

        def progress(stage: str, done: int, total: int, message: str) -> None:
            self.messages.put(("log", instance, f"[{stage}] {message}"))
            self.messages.put(("progress", done, total))

        def work() -> None:
            from gelauncher.core.cancel import Cancelled

            try:
                report = install_version(
                    self.layout,
                    version_id,
                    instance_name=instance,
                    progress=progress,
                    fetcher=fetcher,
                    cancel=token,
                )
                if report.downloads.failures:
                    self.messages.put(
                        ("log", instance, f"[ERROR] {len(report.downloads.failures)} 个文件失败")
                    )
                self.messages.put(
                    ("log", instance, f"[LAUNCHER] 安装完成：下载 {report.downloads.downloaded} 个文件")
                )
                self.messages.put(("status", f"{instance} 安装完成"))
                self.messages.put(("refresh", report.version_id))
                self.messages.put(("open_version", report.version_id))
                self.messages.put(
                    ("task_done", ("ok", f"「{instance}」安装完成"))
                )
            except Cancelled:
                self.cleanup_partial(instance, before_versions, created=True)
                self.messages.put(("log", instance, "[LAUNCHER] 已取消，清理残留文件"))
                self.messages.put(
                    ("task_done", ("cancelled", f"已取消安装「{instance}」，本次产生的文件已清理"))
                )
            except Exception as exc:  # noqa: BLE001
                self.cleanup_partial(instance, before_versions, created=True)
                self.messages.put(("log", instance, f"[ERROR] 安装失败：{exc}"))
                self.messages.put(("task_done", ("error", f"安装「{instance}」失败：{exc}")))
            finally:
                self.messages.put(("idle", None))

        self._start_job(work)
        return None

    def repair_instance(self, instance: str) -> None:
        """补全实例缺的文件（黑屏/缺贴图/没声音基本都是这个原因）。"""
        from gelauncher.core.repair import RepairError, repair_instance

        window = self.open_log_window(instance)
        window.start_session(f"补全 {instance} 缺的文件")
        window.append(f"[LAUNCHER] 游戏目录：{self.layout.root}")
        window.set_status("检查缺什么…")
        fetcher = self.make_fetcher()

        def progress(stage: str, message: str) -> None:
            self.messages.put(("log", instance, f"[{stage}] {message}"))

        def work() -> None:
            try:
                report = repair_instance(
                    self.layout, instance, fetcher=fetcher, progress=progress
                )
                self.messages.put(
                    (
                        "log",
                        instance,
                        f"[LAUNCHER] 补全完成：下载 {report.downloads.downloaded} 个，"
                        f"已存在 {report.downloads.skipped} 个",
                    )
                )
                self.messages.put(("status", f"{instance} 文件已补全"))
            except RepairError as exc:
                self.messages.put(("log", instance, f"[LAUNCHER] {exc}"))
                self.messages.put(("status", str(exc)))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("log", instance, f"[ERROR] 补全失败：{exc}"))
                self.messages.put(("error", f"补全失败：{exc}"))
            finally:
                self.messages.put(("idle", None))

        self._start_job(work)

    def switch_game_dir(self, path: str) -> None:
        """切换/添加游戏目录（版本页左侧目录列表也走这里）。"""
        self.config.add_game_dir(path)
        self.config.save()
        self.layout = GameLayout(Path(path))
        self.show_page("versions")
        self.pages["versions"].refresh_dirs()
        self.pages["versions"].refresh()
        self.set_status(f"游戏目录：{path}")

    # 兼容旧调用点
    def change_game_dir(self, path: str) -> None:
        self.switch_game_dir(path)

    def remove_game_dir(self, path: str) -> None:
        self.config.forget_game_dir(path)
        self.config.save()
        self.layout = GameLayout(self.config.game_dir_path())
        self.show_page("versions")
        self.pages["versions"].refresh_dirs()
        self.pages["versions"].refresh()
        self.set_status(f"已从目录列表移除：{path}")

    # ---------------------------------------------------------------- Java 列表

    def java_choices(self) -> list[str]:
        return self.config.java_choices()

    def add_java_path(self, path: str) -> None:
        if self.config.add_java_path(path):
            self.config.save()

    def detect_java(self) -> None:
        """后台扫一遍本机 Java，把找到的路径全部塞进下拉框。"""

        def work() -> None:
            runtimes = installed_javas()
            pairs = [(item.major, str(item.path)) for item in runtimes]
            changed = False
            for _major, path in pairs:
                if self.config.add_java_path(path):
                    changed = True
            if changed:
                self.config.save()
            self.messages.put(("java_choices", pairs))
            if not pairs:
                self.messages.put(("status", "没找到可用的 Java"))

        self._start_job(work)

    # ---------------------------------------------------------------- 任务与消息

    def _start_job(self, target) -> None:
        if self.busy:
            messagebox.showinfo("等一下", "上一个任务还没结束")
            return
        self.busy = True

        def wrapper() -> None:
            try:
                target()
            finally:
                self.messages.put(("idle", None))

        threading.Thread(target=wrapper, daemon=True).start()

    def _on_callback_exception(self, exc, value, tb) -> None:
        text = "".join(traceback.format_exception(exc, value, tb))
        self._write_crash_log(text)
        messagebox.showerror("界面出错了", f"{value}\n\n详细信息已写入：\n{self.crash_log}")

    def _write_crash_log(self, text: str) -> None:
        try:
            self.crash_log.parent.mkdir(parents=True, exist_ok=True)
            with self.crash_log.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} =====\n")
                handle.write(text)
        except OSError:
            pass

    def _on_close(self) -> None:
        running = [name for name in self.processes if self.is_running(name)]
        if running and not messagebox.askyesno("游戏还在运行", "有游戏在跑，要一起结束吗？"):
            return
        for name in running:
            self.stop(name)
        self.config.window_geometry = self.root.geometry()
        self.config.save()
        for window in list(self.log_windows.values()):
            try:
                window.destroy()
            except tk.TclError:
                pass
        self.root.destroy()

    def _poll(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "log":
                    _, version_id, text = message
                    self.log_line(version_id, text)
                elif kind == "log_status":
                    _, version_id, text = message
                    window = self.log_windows.get(version_id)
                    if window is not None:
                        try:
                            window.set_status(text)
                        except tk.TclError:
                            pass
                elif kind == "task_done":
                    level, text = message[1]
                    self.finish_task(level, text)
                elif kind == "status":
                    self.set_status(message[1])
                elif kind == "progress":
                    page = self.pages.get("versions")
                    if isinstance(page, VersionsPage):
                        page.detail_status.set(f"下载中 {message[1]}/{message[2]}")
                elif kind == "running":
                    page = self.pages.get("versions")
                    if isinstance(page, VersionsPage) and page.current == message[1]:
                        page._refresh_running_state()
                elif kind == "stopped":
                    page = self.pages.get("versions")
                    if isinstance(page, VersionsPage) and page.current == message[1]:
                        page._refresh_running_state()
                    self.processes.pop(message[1], None)
                elif kind == "refresh":
                    target = message[1] if len(message) > 1 else None
                    self.pages["versions"].refresh(target)
                elif kind == "open_version":
                    self.show_page("versions")
                    self.pages["versions"].open(message[1])
                elif kind == "java_choices":
                    self.detected_javas = list(message[1])
                    for page in self.pages.values():
                        refresh = getattr(page, "refresh_java_choices", None)
                        if callable(refresh):
                            try:
                                refresh()
                            except tk.TclError:
                                pass
                elif kind == "java_hint":
                    page = self.pages.get("versions")
                    if page is not None:
                        refresh = getattr(page, "_refresh_java_hint_async", None)
                        if callable(refresh):
                            refresh()
                elif kind == "refresh_java":
                    page = self.pages.get("versions")
                    if page is not None:
                        refresh = getattr(page, "refresh_java_choices", None)
                        if callable(refresh):
                            try:
                                refresh()
                            except tk.TclError:
                                pass
                elif kind == "error":
                    self.set_status(message[1])
                elif kind == "idle":
                    self.busy = False
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


def run_ui(config: Config | None = None) -> int:
    from gelauncher.ui import theme
    from gelauncher.ui.dpi import enable_dpi_awareness

    enable_dpi_awareness()
    config = config or Config.load()
    theme.set_initial(config.appearance)
    root = theme.make_root()
    app = LauncherApp(root, config)
    try:
        root.mainloop()
    except Exception:  # noqa: BLE001
        app._write_crash_log(traceback.format_exc())
        raise
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
