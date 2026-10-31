"""Tkinter 主窗口（方案 M5：列表 / 安装 / 启动 / 日志 / 设置）。

约定（方案 §6.1）：这个文件只负责"收集参数 + 调 core + 显示进度"，
任何解析、下载、拼命令的逻辑都不许写在这里；core 也永远不 import tkinter。

线程模型：Tkinter 只能在主线程更新，所以后台线程只往 ``queue.Queue`` 里丢消息，
主线程用 ``after()`` 轮询消费。
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gelauncher import __version__
from gelauncher.cli import detect_loader
from gelauncher.config import Config
from gelauncher.core.command import Account
from gelauncher.core.install_loader import install_fabric, install_forge
from gelauncher.core.install_vanilla import install_version
from gelauncher.core.java import installed_javas
from gelauncher.core.launch import LaunchOptions, prepare, run
from gelauncher.core.layout import GameLayout, current_env
from gelauncher.core.models import VersionJsonError


class LauncherApp:
    def __init__(self, root: tk.Tk, config: Config) -> None:
        self.root = root
        self.config = config
        self.layout = GameLayout(config.game_dir_path())
        self.messages: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self._current_process = None

        self.game_dir_var = tk.StringVar(value=str(self.layout.root))
        self.username_var = tk.StringVar(value=config.username)
        self.memory_var = tk.StringVar(value=str(config.max_memory_mb))
        self.status_var = tk.StringVar(value="就绪")
        self.java_var = tk.StringVar(value="（自动）")

        self._build()
        self.refresh_versions()
        self.root.after(80, self._poll)

    # ---------------------------------------------------------------- 布局

    def _build(self) -> None:
        self.root.title(f"Ge 启动器 {__version__}")
        self.root.geometry("980x640")
        self.root.minsize(820, 520)

        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        # 顶部：游戏目录
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="游戏目录").pack(side="left")
        ttk.Entry(top, textvariable=self.game_dir_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(top, text="切换…", command=self.choose_game_dir).pack(side="left")
        ttk.Button(top, text="打开", command=self.open_game_dir).pack(side="left", padx=(4, 0))

        middle = ttk.Frame(outer)
        middle.pack(fill="both", expand=True, pady=8)

        # 左：版本列表
        left = ttk.LabelFrame(middle, text="已安装的版本", padding=6)
        left.pack(side="left", fill="both", expand=True)
        columns = ("loader", "java")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="版本")
        self.tree.heading("loader", text="加载器")
        self.tree.heading("java", text="Java")
        self.tree.column("#0", width=300)
        self.tree.column("loader", width=80, anchor="center")
        self.tree.column("java", width=60, anchor="center")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.launch())

        # 右：设置
        right = ttk.LabelFrame(middle, text="这次怎么启动", padding=8)
        right.pack(side="left", fill="y", padx=(8, 0))
        self._row(right, "玩家名", ttk.Entry(right, textvariable=self.username_var, width=18))
        self._row(right, "最大内存(MB)", ttk.Entry(right, textvariable=self.memory_var, width=18))
        self._row(right, "Java", ttk.Label(right, textvariable=self.java_var, width=18))
        ttk.Button(right, text="检测 Java", command=self.detect_java).pack(fill="x", pady=(6, 2))

        actions = ttk.LabelFrame(outer, text="操作", padding=6)
        actions.pack(fill="x")
        row = ttk.Frame(actions)
        row.pack(fill="x")
        self.launch_button = ttk.Button(row, text="▶ 启动", command=self.launch)
        self.launch_button.pack(side="left")
        ttk.Button(row, text="打印启动命令", command=self.print_command).pack(side="left", padx=6)
        ttk.Button(row, text="刷新列表", command=self.refresh_versions).pack(side="left")

        row2 = ttk.Frame(actions)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="安装原版…", command=self.install_vanilla).pack(side="left")
        ttk.Button(row2, text="给选中版本装 Fabric", command=self.install_fabric).pack(side="left", padx=6)
        ttk.Button(row2, text="给选中版本装 Forge", command=self.install_forge).pack(side="left")
        self.progress = ttk.Progressbar(row2, mode="determinate", length=220)
        self.progress.pack(side="right")

        # 底部：日志
        bottom = ttk.LabelFrame(outer, text="日志", padding=4)
        bottom.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(bottom, height=10, wrap="none", state="disabled")
        log_scroll = ttk.Scrollbar(bottom, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        ttk.Label(outer, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(6, 0))

    def _row(self, parent, label: str, widget) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=label, width=12).pack(side="left")
        widget.pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------------- 工具

    def log_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def selected_version(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def base_mc_version(self, version_id: str) -> str:
        """选中版本对应的原版 id（Fabric/Forge 实例要往回找 inheritsFrom）。"""
        try:
            version = self.layout.load_json(version_id)
        except VersionJsonError:
            return version_id
        chain = version_id
        seen = set()
        while version.inherits_from and version.inherits_from not in seen:
            seen.add(version.inherits_from)
            chain = version.inherits_from
            try:
                version = self.layout.load_json(chain)
            except VersionJsonError:
                break
        return chain

    def _run_background(self, target, *args) -> None:
        if self.busy:
            messagebox.showinfo("等一下", "上一个任务还没结束")
            return
        self.busy = True
        self.launch_button.configure(state="disabled")

        def wrapper() -> None:
            try:
                target(*args)
            except Exception as exc:  # noqa: BLE001 - 后台错误要显示到界面
                self.messages.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                self.messages.put(("idle", None))

        threading.Thread(target=wrapper, daemon=True).start()

    # ---------------------------------------------------------------- 版本列表

    def refresh_versions(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.layout.root.is_dir():
            self.status_var.set(f"游戏目录不存在：{self.layout.root}")
            return
        env = current_env()
        for version_id in self.layout.installed_versions():
            try:
                version = self.layout.load_json(version_id)
            except VersionJsonError as exc:
                self.tree.insert("", "end", iid=version_id, text=f"{version_id}（JSON 有问题）", values=("?", "?"))
                self.log_line(f"[警告] {version_id} 的 JSON 有问题：{exc}")
                continue
            self.tree.insert(
                "",
                "end",
                iid=version_id,
                text=version_id,
                values=(detect_loader(version), version.java_version or "?"),
            )
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
        self.status_var.set(f"{self.layout.root} · {env.os_name}/{env.arch} · {len(children)} 个版本")

    def choose_game_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.layout.root), title="选 .minecraft 目录")
        if not chosen:
            return
        self.config.game_dir = chosen
        self.config.save()
        self.layout = GameLayout(Path(chosen))
        self.game_dir_var.set(chosen)
        self.refresh_versions()

    def open_game_dir(self) -> None:
        path = self.layout.root
        if not path.is_dir():
            messagebox.showwarning("没这个目录", str(path))
            return
        os.startfile(str(path))  # noqa: S606 - Windows 上就是用资源管理器打开

    def detect_java(self) -> None:
        runtimes = installed_javas()
        if not runtimes:
            self.java_var.set("（没找到 Java）")
            return
        description = "，".join(f"Java {item.major}" for item in runtimes)
        self.java_var.set(description)

    # ---------------------------------------------------------------- 启动

    def _account(self) -> Account:
        return Account(name=self.username_var.get().strip() or "Player")

    def _options(self, version_id: str, dry_run: bool = False) -> LaunchOptions:
        memory = int(self.memory_var.get() or 2048)
        return LaunchOptions(
            layout=self.layout,
            version_id=version_id,
            account=self._account(),
            max_memory_mb=memory,
            dry_run=dry_run,
        )

    def print_command(self) -> None:
        version_id = self.selected_version()
        if not version_id:
            return
        try:
            plan = prepare(self._options(version_id, dry_run=True))
        except Exception as exc:  # noqa: BLE001
            self.log_line(f"[错误] 拼不出命令：{exc}")
            messagebox.showerror("拼不出命令", str(exc))
            return
        self.log_line("=" * 60)
        self.log_line(plan.printable_command())
        self.log_line("=" * 60)
        for warning in plan.warnings:
            self.log_line(f"[警告] {warning}")

    def launch(self) -> None:
        version_id = self.selected_version()
        if not version_id:
            messagebox.showinfo("先选一个版本", "左边列表里点一个版本")
            return
        self._save_settings()

        def work() -> None:
            self.messages.put(("log", f"准备启动 {version_id}…"))
            plan = prepare(self._options(version_id))
            for warning in plan.warnings:
                self.messages.put(("log", f"[警告] {warning}"))
            self.messages.put(("status", f"{version_id} 运行中（Java {plan.java.major}）"))
            log_path = self.layout.root / "logs" / "gelauncher-ui.log"
            self.messages.put(("log", f"日志文件：{log_path}"))
            process = run(
                plan,
                log_path=log_path,
                on_line=lambda line: self.messages.put(("log", line)),
                wait=False,
            )
            self._current_process = process
            code = process.wait()
            self.messages.put(("status", f"{version_id} 已退出（退出码 {code}）"))
            self.messages.put(("log", f"游戏退出，退出码 {code}"))

        self._run_background(work)

    # ---------------------------------------------------------------- 安装

    def install_vanilla(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("安装原版")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="版本 id（例如 1.21.8、1.21.4）").pack(padx=12, pady=(12, 4))
        entry = ttk.Entry(dialog, width=24)
        entry.pack(padx=12)
        entry.insert(0, self.selected_version() or "1.21.8")
        entry.focus_set()

        def submit() -> None:
            version_id = entry.get().strip()
            dialog.destroy()
            if version_id:
                self._start_install(version_id)

        ttk.Button(dialog, text="开始下载", command=submit).pack(padx=12, pady=12)

    def _start_install(self, version_id: str) -> None:
        self._save_settings()

        def work() -> None:
            self.messages.put(("status", f"安装 {version_id}…"))
            report = install_version(
                self.layout,
                version_id,
                progress=lambda stage, done, total, message: self.messages.put(
                    ("progress", stage, done, total, message)
                ),
            )
            for message in report.messages:
                self.messages.put(("log", f"[提示] {message}"))
            if report.downloads.failures:
                self.messages.put(
                    ("log", f"{len(report.downloads.failures)} 个文件失败：" + str(report.downloads.failures[:3]))
                )
            self.messages.put(("log", f"安装完成：下载 {report.downloads.downloaded} 个文件"))
            self.messages.put(("refresh", None))

        self._run_background(work)

    def install_fabric(self) -> None:
        version_id = self.selected_version()
        if not version_id:
            messagebox.showinfo("先选一个版本", "选一个原版版本，再给它装 Fabric")
            return
        base = self.base_mc_version(version_id)
        if not messagebox.askyesno("装 Fabric", f"给 {base} 装最新稳定版 Fabric？"):
            return

        def work() -> None:
            report = install_fabric(
                self.layout,
                base,
                progress=lambda stage, message: self.messages.put(("log", f"[{stage}] {message}")),
            )
            self.messages.put(("log", f"装好了：{report.version_id}"))
            self.messages.put(("refresh", None))

        self._run_background(work)

    def install_forge(self) -> None:
        version_id = self.selected_version()
        if not version_id:
            messagebox.showinfo("先选一个版本", "选一个原版版本，再给它装 Forge")
            return
        base = self.base_mc_version(version_id)
        if not messagebox.askyesno("装 Forge", f"给 {base} 装 Forge（版本自动挑推荐的）？"):
            return

        def work() -> None:
            report = install_forge(
                self.layout,
                base,
                progress=lambda stage, message: self.messages.put(("log", f"[{stage}] {message}")),
            )
            self.messages.put(("log", f"装好了：{report.version_id}"))
            self.messages.put(("refresh", None))

        self._run_background(work)

    # ---------------------------------------------------------------- 消息循环

    def _save_settings(self) -> None:
        self.config.username = self.username_var.get().strip() or "Player"
        try:
            self.config.max_memory_mb = int(self.memory_var.get())
        except ValueError:
            self.config.max_memory_mb = 2048
        self.config.save()

    def _poll(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "log":
                    self.log_line(message[1])
                elif kind == "status":
                    self.status_var.set(message[1])
                elif kind == "progress":
                    _, stage, done, total, text = message
                    if total:
                        self.progress.configure(maximum=total, value=done)
                    self.status_var.set(f"{stage}：{text[:60]}")
                elif kind == "error":
                    self.log_line(f"[错误] {message[1]}")
                    self.status_var.set("出错了，看日志")
                elif kind == "refresh":
                    self.refresh_versions()
                elif kind == "idle":
                    self.busy = False
                    self.launch_button.configure(state="normal")
                    self.progress.configure(value=0)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


def run_ui(config: Config | None = None) -> int:
    config = config or Config.load()
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    LauncherApp(root, config)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
