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
        self.nav_buttons: dict[str, object] = {}

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
        # 界面已全部换成 CustomTkinter，不再需要 ttk 样式
        return

    # ---------------------------------------------------------------- 布局

    def _build(self) -> None:
        self.root.title(f"Ge 启动器 {__version__}")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=0)

        from gelauncher.ui import theme

        scale = getattr(self, "scale", 1.0)
        if theme.is_ctk(self.root):
            self.root.minsize(920, 560)
        else:
            self.root.minsize(int(920 * scale), int(560 * scale))
        import customtkinter as ctk

        from gelauncher.ui.titlebar import TitleBar
        from gelauncher.ui.winframe import hide_native_titlebar

        self.titlebar = TitleBar(
            self.root, title=f"Ge 启动器 {__version__}", on_close=self._on_close
        )
        self.titlebar.grid(row=0, column=0, columnspan=2, sticky="ew")
        # 去掉系统标题栏（保留窗口边框，仍可缩放/贴边分屏）
        self.root.after(30, lambda: hide_native_titlebar(self.root))

        sidebar = ctk.CTkFrame(self.root, corner_radius=0, width=150)
        sidebar.grid(row=1, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        for index, (key, label, icon) in enumerate(NAV_ITEMS, start=0):
            button = ctk.CTkButton(
                sidebar,
                text=f"{icon}  {label}",
                anchor="w",
                height=36,
                corner_radius=8,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                fg_color="transparent",
                text_color=theme.TEXT,
                hover_color=("#e2e8f0", "#2a2f37"),
                command=lambda name=key: self.show_page(name),
            )
            button.grid(row=index, column=0, sticky="ew", padx=8, pady=2)
            self.nav_buttons[key] = button
        sidebar.rowconfigure(len(NAV_ITEMS), weight=1)
        self.status_label = ctk.CTkLabel(
            sidebar,
            textvariable=self.status_var,
            text_color=theme.MUTED,
            wraplength=130,
            justify="left",
            anchor="sw",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )
        self.status_label.grid(
            row=len(NAV_ITEMS) + 1, column=0, sticky="ew", padx=10, pady=(0, 10)
        )
        self.content = ctk.CTkFrame(self.root, corner_radius=0, fg_color=theme.BG)

        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.pages = {
            "versions": VersionsPage(self.content, self),
            "accounts": AccountsPage(self.content, self),
            "settings": SettingsPage(self.content, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            page.grid_remove()

        self.current_page = ""
        self._launching = False
        self._selected_java: Path | None = None

    def _preload(self) -> None:
        """启动后预热首次点击会用到的东西：Java 探测、jar 内嵌版本号、实例 JSON。"""
        import threading

        paths = list(self.java_choices())
        layout = self.layout

        def work() -> None:
            from gelauncher.core.java import installed_javas, probe_java_cached

            for path in paths:
                try:
                    probe_java_cached(path)
                except Exception:  # noqa: BLE001
                    pass
            try:
                # 顺手扫一遍本机 Java，把探测缓存预热；点启动时的同步检测就能瞬间完成
                installed_javas()
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
                page.grid(padx=10, pady=10)
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

    def instance_key(self, version_id: str, root=None) -> str:
        """运行状态/日志窗口的键：完整游戏目录 + 实例名（同名实例不同目录互不影响）。"""
        import os

        base = Path(root) if root is not None else self.layout.root
        try:
            base = base.resolve()
        except OSError:
            pass
        return f"{os.path.normcase(str(base))}|{version_id}"

    def log_line(self, target: str, text: str) -> None:
        key = target if "|" in target else self.instance_key(target)
        window = self.log_windows.get(key)
        if window is not None:
            try:
                window.append(text)
            except tk.TclError:
                self.log_windows.pop(key, None)

    # ---------------------------------------------------------------- 日志窗口

    def open_log_window(
        self, version_id: str, *, task: bool = False, on_cancel=None
    ) -> LogWindow:
        key = self.instance_key(version_id)
        existing = self.log_windows.get(key)
        if existing is not None and not task:
            try:
                existing.deiconify()
                existing.lift()
                return existing
            except tk.TclError:
                self.log_windows.pop(key, None)
        if existing is not None and task:
            # 安装/导入要用带取消按钮的窗口，旧日志窗口先关掉
            self.log_windows.pop(key, None)
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
        window.instance_key = key

        def forget() -> None:
            self.log_windows.pop(key, None)
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", forget)
        self.log_windows[key] = window
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
        key = getattr(window, "instance_key", None)
        if key:
            self.log_windows.pop(key, None)
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass
        if level == "ok":
            messagebox.showinfo("完成", message)
        elif level == "warning":
            messagebox.showwarning("导入不完整", message)
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
        process = self.processes.get(self.instance_key(version_id))
        return bool(process is not None and process.poll() is None)

    # ---------------------------------------------------------------- Java 自动安装

    def ensure_java(self, version_id: str) -> bool:
        """检查版本要求的 Java；缺了就提示自动下载。返回 False 表示先别启动。"""
        self._selected_java = None
        from gelauncher.core.java import (
            choose_java,
            java_major_compatible,
            probe_java,
        )
        from gelauncher.core.models import VersionJsonError

        try:
            version = self.layout.load_json(version_id)
        except VersionJsonError:
            return True
        required = version.java_version
        if not required:
            return True

        settings = self.config.instance(version_id, root=self.layout.root)
        manual = str(settings.get("java_path") or "").strip()
        if manual:
            runtime = probe_java(manual)
            if runtime is not None and java_major_compatible(required, runtime.major):
                self._selected_java = Path(manual)
                return True
            if runtime is None:
                reason = f"手动指定的 Java 跑不起来：{manual}"
            elif required <= 8 and runtime.major != 8:
                reason = (
                    f"手动指定的 Java {runtime.major} 太新，这个版本需要 Java 8"
                )
            else:
                reason = f"手动指定的 Java {runtime.major} 低于要求的 Java {required}"
            if not messagebox.askyesno(
                "Java 不符合要求",
                f"{reason}。\n\n是否自动下载安装 Temurin JDK {required}？（约 190MB）",
            ):
                return True
            return self._start_java_install(required)

        # 每次启动前自动检测一次（缓存探测，很快），
        # 避免"装了 Java 但从没点过检测"导致误报缺 Java
        self.set_status("正在检测本机 Java…")
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        self.scan_installed_javas()
        runtime, satisfied = choose_java(
            required, self.java_choices(), preferred=self.config.java_path
        )
        if satisfied and runtime is not None and not java_major_compatible(
            required, runtime.major
        ):
            satisfied = False
        if satisfied:
            if runtime is not None:
                self._selected_java = runtime.path
            return True
        found = []
        for item in self.java_choices():
            probed = probe_java(item)
            if probed is not None:
                found.append(f"Java {probed.major}")
        have = "、".join(sorted(set(found))) or "无"
        if required <= 8:
            have = f"{have}（这个版本需要 Java 8，更高版本不兼容）"
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
        from gelauncher.config import managed_java_dir

        window.append(f"[LAUNCHER] 安装位置：{managed_java_dir()}")
        window.set_status(f"下载 Java {required}…")
        fetcher = self.make_fetcher()

        def progress(stage: str, message: str) -> None:
            if stage == "java" and message.startswith("下载 Java"):
                # 进度只更新标题栏状态，避免每 1% 刷一行日志
                self.messages.put(("log_status", instance, message))
                return
            self.messages.put(("log", instance, f"[{stage}] {message}"))

        def work() -> None:
            from gelauncher.core.cancel import Cancelled

            try:
                runtime = install_java(
                    managed_java_dir(),
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
        # 防止连点：ensure_java 会同步扫描 Java，期间再点一次会重复启动
        if self._launching:
            return
        self._launching = True
        try:
            self._launch_impl(version_id)
        finally:
            self._launching = False

    def _launch_impl(self, version_id: str) -> None:
        if self.busy:
            messagebox.showinfo("等一下", "上一个任务还没结束")
            return
        if self.is_running(version_id):
            messagebox.showinfo("已经在跑了", f"{version_id} 已在运行")
            return
        if not self.ensure_java(version_id):
            return

        settings = self.config.instance(version_id, root=self.layout.root)
        window = self.open_log_window(version_id) if settings.get("show_log_window", True) else None
        if window is not None:
            window.start_session(f"启动 {version_id}")
            window.set_status("准备启动…")

        def build_options(account) -> LaunchOptions:
            return LaunchOptions(
                layout=self.layout,
                version_id=version_id,
                account=account,
                java_path=(
                    Path(settings["java_path"])
                    if settings.get("java_path")
                    else self._selected_java
                ),
                max_memory_mb=int(settings["max_memory_mb"]),
                width=settings.get("width"),
                height=settings.get("height"),
                extra_jvm_args=self._split_jvm_args(settings),
            )

        key = self.instance_key(version_id)

        def work() -> None:
            try:
                self.messages.put(("log", key, f"[LAUNCHER] 账号：{self.config.selected_account}"))
                account = self.designated_account()
                plan = prepare(build_options(account))
                for warning in plan.warnings:
                    self.messages.put(("log", key, f"[WARN] {warning}"))
                log_path = self.layout.root / "logs" / f"gelauncher-{version_id}.log"
                self.messages.put(("log", key, f"[LAUNCHER] 日志文件：{log_path}"))
                process = run(
                    plan,
                    log_path=log_path,
                    on_line=lambda line: self.messages.put(("log", key, line)),
                    wait=False,
                )
                self.processes[key] = process
                priority = str(settings.get("process_priority") or "normal")
                if priority != "normal":
                    from gelauncher.core.process_priority import apply_priority

                    apply_priority(process.pid, priority)
                    self.messages.put(("log", key, f"[LAUNCHER] 进程优先级：{priority}"))
                self.messages.put(("status", f"{version_id} 运行中（Java {plan.java.major}）"))
                self.messages.put(("running", key))
                if window is not None:
                    self.messages.put(("log_status", key, "游戏运行中…"))
            except Exception as exc:  # noqa: BLE001 - 启动失败要显示出来
                self.messages.put(("log", key, f"[ERROR] 启动失败：{exc}"))
                if window is not None:
                    self.messages.put(("log_status", key, "启动失败"))
                self.messages.put(("status", f"启动 {version_id} 失败"))
                self.messages.put(("error", f"启动失败：{exc}"))
                if window is None:
                    self.messages.put(
                        ("launch_failed", version_id, str(exc), str(log_path))
                    )
                self.messages.put(("stopped", key))
                return

            # 等待游戏退出放到另一个线程：游戏跑着的时候界面不能被 busy 锁住
            def waiter() -> None:
                code = process.wait()
                self.messages.put(("log", key, f"[LAUNCHER] 游戏退出，退出码 {code}"))
                self.messages.put(("log_status", key, f"已退出（退出码 {code}）"))
                self.messages.put(("status", f"{version_id} 已退出（退出码 {code}）"))
                if code != 0:
                    self.messages.put(
                        ("launch_crashed", version_id, code, str(log_path))
                    )
                self.messages.put(("stopped", key))

            threading.Thread(target=waiter, daemon=True).start()

        self._start_job(work)

    def stop(self, target: str) -> None:
        key = target if "|" in target else self.instance_key(target)
        process = self.processes.get(key)
        if process is not None and process.poll() is None:
            process.terminate()
            name = key.split("|", 1)[-1]
            self.set_status(f"正在结束 {name}…")

    # ---------------------------------------------------------------- 安装 / 命令

    def base_mc_version(self, version_id: str) -> str:
        """实例对应的真实游戏版本（实例名可以和版本号不同）。"""
        return self.layout.actual_version(version_id)

    def _split_jvm_args(self, settings: dict) -> tuple[str, ...]:
        """把实例设置里的 JVM 参数字符串拆成 argv（引号不配对时按空格兜底）。"""
        import shlex

        text = str(settings.get("jvm_args") or "").strip()
        if not text:
            return ()
        try:
            return tuple(shlex.split(text))
        except ValueError:
            return tuple(text.split())

    def designated_account(self) -> Account:
        """本次启动用的账号（微软账号会自动刷新 token）。"""
        from gelauncher.core.accounts import resolve_account

        return resolve_account(self.config)

    def print_command(self, version_id: str) -> None:
        settings = self.config.instance(version_id, root=self.layout.root)
        try:
            account = self.designated_account()
        except Exception:  # noqa: BLE001 - 只是打印命令，别因为登录拦住
            account = Account.offline(self.config.selected_account or "Player")
        options = LaunchOptions(
            layout=self.layout,
            version_id=version_id,
            account=account,
            java_path=(
                Path(settings["java_path"])
                if settings.get("java_path")
                else self._selected_java
            ),
            max_memory_mb=int(settings["max_memory_mb"]),
            extra_jvm_args=self._split_jvm_args(settings),
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
            if stage == "java" and message.startswith("下载 Java"):
                # 进度只更新标题栏状态，避免每 1% 刷一行日志
                self.messages.put(("log_status", instance, message))
                return
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
                if getattr(report, "complete", True):
                    self.messages.put(("task_done", ("ok", f"整合包「{instance}」导入完成")))
                else:
                    missing = getattr(report, "unresolved", 0) + len(report.downloads.failures)
                    self.messages.put(
                        (
                            "task_done",
                            (
                                "warning",
                                f"整合包「{instance}」导入完成，但有 {missing} 个文件没装上，"
                                "游戏可能起不来。\n\n详见日志窗口里的 [files]/[curse] 记录。",
                            ),
                        )
                    )
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
            if stage == "java" and message.startswith("下载 Java"):
                # 进度只更新标题栏状态，避免每 1% 刷一行日志
                self.messages.put(("log_status", instance, message))
                return
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
            if stage == "java" and message.startswith("下载 Java"):
                # 进度只更新标题栏状态，避免每 1% 刷一行日志
                self.messages.put(("log_status", instance, message))
                return
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

    def _apply_java_scan(self, runtimes) -> list[tuple[int, str]]:
        """把一次扫描结果落盘：清理失效路径、去重、同步候选列表和界面列表。"""
        from pathlib import Path

        from gelauncher.core.java import probe_java_cached

        combined = list(runtimes)
        known_paths = {str(item.path).lower() for item in combined}
        known_versions = {
            (item.major, getattr(item, "version", "")) for item in combined
        }
        # 用户手动加的、但仍存在的 Java 也要保留；
        # 但如果只是同一版本的别名（javapath）或 JDK 自带 jre，就不要重复加回来
        from gelauncher.core.java_scan import is_under_jre

        for path in list(self.config.java_paths):
            try:
                candidate = Path(path)
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            if path.lower() in known_paths or is_under_jre(candidate):
                continue
            probed = probe_java_cached(path)
            if probed is None:
                continue
            key = (probed.major, getattr(probed, "version", ""))
            if key in known_versions:
                continue
            known_paths.add(path.lower())
            known_versions.add(key)
            combined.append(probed)

        seen: set[str] = set()
        pairs: list[tuple[int, str]] = []
        for runtime in sorted(combined, key=lambda item: (-item.major, str(item.path).lower())):
            key = str(runtime.path).lower()
            if key in seen:
                continue
            seen.add(key)
            pairs.append((runtime.major, str(runtime.path)))

        self.config.java_paths = [path for _major, path in pairs]
        if self.config.java_path and self.config.java_path not in self.config.java_paths:
            # 旧别名（javapath / jre）不在去重后的列表里，就别再当默认值插回去
            self.config.java_path = ""
        self.config.save()
        self.detected_javas = list(pairs)
        self.messages.put(("java_choices", pairs))
        return pairs

    def scan_installed_javas(self) -> list[tuple[int, str]]:
        """同步扫一遍本机 Java（探测有缓存），结果并入下拉候选。

        启动游戏前调用，保证"装过但没点检测"的 Java 也能被用上。
        """
        from gelauncher.config import managed_java_dir
        from gelauncher.core.java_scan import collect_java_runtimes

        runtimes = collect_java_runtimes(self.layout.root, managed_java_dir())
        return self._apply_java_scan(runtimes)

    def detect_java(self) -> None:
        """后台扫一遍本机 Java（含启动器自己管理的目录），把结果同步到界面。"""

        def work() -> None:
            try:
                from gelauncher.config import managed_java_dir
                from gelauncher.core.java_scan import collect_java_runtimes

                runtimes = collect_java_runtimes(self.layout.root, managed_java_dir())
                pairs = self._apply_java_scan(runtimes)
                if not pairs:
                    self.messages.put(("status", "没找到可用的 Java"))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("status", f"检测 Java 失败：{exc}"))

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
        # 关窗时 CTk 的悬停动画可能回调已销毁的控件，这类 TclError 无害，不弹框
        if isinstance(value, tk.TclError) and "invalid command name" in str(value):
            return
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
        running = [
            key for key in list(self.processes) if self.processes[key].poll() is None
        ]
        if running and not messagebox.askyesno("游戏还在运行", "有游戏在跑，要一起结束吗？"):
            return
        for key in running:
            self.stop(key)
        self.config.window_geometry = self.root.geometry()
        self.config.save()
        for window in list(self.log_windows.values()):
            try:
                window.destroy()
            except tk.TclError:
                pass
        self.root.destroy()

    def _show_launch_failure(
        self, version_id: str, detail: str, log_path: str, *, crashed: bool, code: int = 0
    ) -> None:
        """没开日志窗口时，启动失败/异常退出也要让用户看到，并能一键看日志。"""
        from pathlib import Path

        from gelauncher.ui.confirm import ask_confirm

        if self.log_windows.get(self.instance_key(version_id)) is not None:
            # 日志窗口本来就开着，错误已经能看到了，不再打断用户
            return

        tail = ""
        try:
            path = Path(log_path)
            if path.is_file():
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "\n".join(lines[-12:])
        except OSError:
            tail = ""

        if crashed:
            title = "游戏异常退出"
            head = f"「{version_id}」启动后异常退出（退出码 {code}）。"
        else:
            title = "启动失败"
            head = f"「{version_id}」启动失败：\n{detail}"
        message = head
        if tail:
            message += f"\n\n最后日志：\n{tail}"
        elif log_path:
            message += f"\n\n日志文件：{log_path}"

        if ask_confirm(
            self.root,
            title=title,
            message=message,
            confirm_text="查看日志",
            cancel_text="关闭",
        ):
            self.open_log_window(version_id)
            if tail:
                try:
                    self.log_line(version_id, "—— 日志文件最后 12 行 ——")
                    for line in tail.splitlines():
                        self.log_line(version_id, line)
                except tk.TclError:
                    pass

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
                    window = self.log_windows.get(version_id) or self.log_windows.get(
                        self.instance_key(version_id)
                    )
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
                    name = str(message[1]).split("|", 1)[-1]
                    if isinstance(page, VersionsPage) and page.current == name:
                        page._refresh_running_state()
                elif kind == "stopped":
                    page = self.pages.get("versions")
                    name = str(message[1]).split("|", 1)[-1]
                    if isinstance(page, VersionsPage) and page.current == name:
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
                elif kind == "launch_failed":
                    self._show_launch_failure(
                        message[1], message[2], message[3], crashed=False
                    )
                elif kind == "launch_crashed":
                    self._show_launch_failure(
                        message[1], "", message[3], crashed=True, code=message[2]
                    )
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
