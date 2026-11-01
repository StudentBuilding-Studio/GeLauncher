"""主窗口里的三个一级页面：版本、账号、设置。

版本页是"列表 → 单击进入二级页"的结构（参考 HMCL）：二级页里放这个版本自己的设置，
包括是否启用独立日志窗口。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gelauncher import __version__
from gelauncher.cli import detect_loader
from gelauncher.core.java import installed_javas
from gelauncher.core.layout import current_env
from gelauncher.core.models import VersionJsonError


def _same_path(left, right) -> bool:
    """Windows 路径比较：忽略大小写和正/反斜杠。"""
    import os

    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


class Page(ttk.Frame):
    """页面基类：统一 padding 和"切到本页时刷新"的入口。"""

    title = "页面"

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, padding=10)
        self.app = app

    def on_show(self) -> None:  # pragma: no cover - 子类按需覆盖
        pass


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #


class VersionsPage(Page):
    #: 下拉框里的"自动"选项
    AUTO_JAVA = "自动（按版本要求）"

    title = "版本"

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.current: str | None = None
        self.filter_var = tk.StringVar()

        self.list_frame = ttk.Frame(self)
        self.detail_frame = ttk.Frame(self)
        for frame in (self.list_frame, self.detail_frame):
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)

        self._build_list()
        self._build_detail()
        self.show_list()

    # ---------------------------------------------------------------- 列表

    def _build_list(self) -> None:
        header = ttk.Frame(self.list_frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="版本", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.dir_hint_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.dir_hint_var, foreground="#666").grid(
            row=0, column=1, sticky="w", padx=10
        )

        body = ttk.Frame(self.list_frame)
        body.grid(row=1, column=0, sticky="nsew")
        self.list_frame.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=0, minsize=230)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_dir_list(body)

        versions = ttk.LabelFrame(body, text="已安装的版本", padding=6)
        versions.grid(row=0, column=1, sticky="nsew")
        versions.columnconfigure(0, weight=1)
        versions.rowconfigure(1, weight=1)

        # 过滤 / 安装原版 / 刷新 都放进"已安装的版本"板块里
        toolbar = ttk.Frame(versions)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="过滤").grid(row=0, column=0, padx=(0, 4))
        entry = ttk.Entry(toolbar, textvariable=self.filter_var, width=18)
        entry.grid(row=0, column=1, sticky="w")
        entry.bind("<KeyRelease>", lambda _e: self.refresh())
        ttk.Button(toolbar, text="安装原版…", width=11, command=self._install_vanilla).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(toolbar, text="导入整合包…", width=12, command=self._import_modpack).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Button(toolbar, text="刷新", width=8, command=self.refresh).grid(
            row=0, column=4, padx=(6, 0)
        )

        from gelauncher.ui.instance_list import InstanceList

        scale = getattr(self.app, "scale", 1.0)
        self.tree = InstanceList(
            versions,
            columns=("version", "loader", "java"),
            headers=("实例", "版本", "加载器", "Java"),
            column_widths=(
                int(260 * scale),
                int(110 * scale),
                int(100 * scale),
                int(80 * scale),
            ),
            on_row=self.open,
        )
        self.tree.grid(row=1, column=0, columnspan=2, sticky="nsew")

    def _build_dir_list(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="目录列表", padding=6)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        from gelauncher.ui.instance_list import InstanceList

        self.dir_tree = InstanceList(
            frame,
            headers=("游戏目录",),
            column_widths=(int(230 * getattr(self.app, "scale", 1.0)),),
            on_row=self._on_dir_row,
        )
        self.dir_tree.grid(row=0, column=0, columnspan=2, sticky="nsew")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="添加目录…", command=self._add_dir).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(buttons, text="打开目录", command=self._open_dir).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(frame, text="从列表移除", command=self._remove_dir).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

    def refresh_dirs(self) -> None:
        self.dir_tree.delete(*self.dir_tree.get_children())
        for directory in self.app.config.game_dirs:
            label = Path(directory).name or directory
            current = _same_path(directory, self.app.layout.root)
            self.dir_tree.insert(
                "",
                "end",
                iid=directory,
                text=f"{'● ' if current else ''}{label}",
                values=(directory,),
            )
        current = str(self.app.layout.root)
        if current in self.dir_tree.get_children():
            self.dir_tree.selection_set(current)
            self.dir_tree.see(current)

    def _on_dir_selected(self, _event) -> None:
        selection = self.dir_tree.selection()
        if not selection:
            return
        directory = selection[0]
        if _same_path(directory, self.app.layout.root):
            return
        self.app.switch_game_dir(directory)

    def _add_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="选 .minecraft 目录", initialdir=str(self.app.layout.root)
        )
        if chosen:
            self.app.switch_game_dir(chosen)

    def _remove_dir(self) -> None:
        selection = self.dir_tree.selection()
        if not selection:
            return
        directory = selection[0]
        if len(self.app.config.game_dirs) <= 1:
            messagebox.showinfo("只有一个目录", "至少保留一个游戏目录")
            return
        self.app.remove_game_dir(directory)

    def _open_dir(self) -> None:
        import os

        selection = self.dir_tree.selection()
        path = selection[0] if selection else str(self.app.layout.root)
        if Path(path).is_dir():
            os.startfile(path)  # noqa: S606
        else:
            messagebox.showwarning("目录不存在", path)

    def _on_click(self, event) -> None:
        # 兼容旧调用；点击已由 InstanceList 的 on_row 回调处理
        row = self.tree.selection()
        if row:
            self.open(row[0])

    def _on_dir_row(self, directory: str) -> None:
        if _same_path(directory, self.app.layout.root):
            return
        self.app.switch_game_dir(directory)

    def _install_vanilla(self) -> None:
        from gelauncher.ui.install_dialog import VanillaInstallDialog

        VanillaInstallDialog(self, self.app, self.app.install_vanilla)

    def _import_modpack(self) -> None:
        from tkinter import filedialog

        from gelauncher.core.modpack import ModpackError, detect
        from gelauncher.ui.import_dialog import ModpackImportDialog

        path = filedialog.askopenfilename(
            title="选整合包",
            filetypes=[("整合包", "*.zip *.mrpack"), ("全部文件", "*.*")],
            parent=self,
        )
        if not path:
            return
        archive = Path(path)
        try:
            info = detect(archive)
        except ModpackError as exc:
            messagebox.showerror("认不出整合包", str(exc), parent=self)
            return
        ModpackImportDialog(self, self.app, archive, info, self.app.import_modpack)

    def refresh(self, select: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        layout = self.app.layout
        self.dir_hint_var.set(f"当前目录：{layout.root}")
        if not layout.root.is_dir():
            self.app.set_status(f"游戏目录不存在：{layout.root}")
            return
        keyword = self.filter_var.get().strip().lower()
        count = 0
        for version_id in layout.installed_versions():
            if keyword and keyword not in version_id.lower():
                continue
            try:
                version = layout.load_json(version_id)
            except VersionJsonError as exc:
                self.tree.insert(
                    "",
                    "end",
                    iid=version_id,
                    text=f"{version_id}（JSON 有问题）",
                    values=("?", "?", "?"),
                )
                self.app.notify(f"{version_id} 的 JSON 有问题：{exc}")
                continue
            self.tree.insert(
                "",
                "end",
                iid=version_id,
                text=version_id,
                values=(
                    layout.actual_version(version_id),
                    detect_loader(version),
                    version.java_version or "?",
                ),
            )
            count += 1
        env = current_env()
        self.app.set_status(
            f"{layout.root} · {env.os_name}/{env.arch} · 共 {count} 个实例"
        )
        # 刚装完的版本要能一眼看到：选中并滚到它
        if select and select in self.tree.get_children():
            self.tree.selection_set(select)
            self.tree.see(select)

    # ---------------------------------------------------------------- 二级页

    def _build_detail(self) -> None:
        header = ttk.Frame(self.detail_frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="← 返回", width=9, command=self.show_list).grid(row=0, column=0)
        self.title_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.title_var, font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=1, sticky="w", padx=10
        )
        self.detail_status = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.detail_status, foreground="#666").grid(
            row=0, column=2, sticky="e"
        )

        body = ttk.Frame(self.detail_frame)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        info = ttk.LabelFrame(body, text="这个版本", padding=10)
        info.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        info.columnconfigure(1, weight=1)
        self.info_vars = {
            key: tk.StringVar(value="-")
            for key in ("mainClass", "实例", "版本", "assets", "库", "隔离目录")
        }
        for index, (label, key) in enumerate(
            (
                ("主类", "mainClass"),
                ("实例", "实例"),
                ("游戏版本", "版本"),
                ("资源索引", "assets"),
                ("库数量", "库"),
                ("游戏目录", "隔离目录"),
            )
        ):
            ttk.Label(info, text=label, width=9).grid(row=index, column=0, sticky="nw", pady=2)
            ttk.Label(info, textvariable=self.info_vars[key], wraplength=360).grid(
                row=index, column=1, sticky="w", pady=2
            )

        ttk.Button(info, text="删除这个实例…", command=self._delete_version).grid(
            row=len(self.info_vars), column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        settings = ttk.LabelFrame(body, text="实例设置", padding=10)
        settings.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="最大内存").grid(row=0, column=0, sticky="w", pady=3)
        memory_row = ttk.Frame(settings)
        memory_row.grid(row=0, column=1, sticky="ew", pady=3)
        memory_row.columnconfigure(0, weight=1)
        self.memory_var = tk.StringVar()
        ttk.Spinbox(
            memory_row, from_=512, to=32768, increment=512, textvariable=self.memory_var, width=10
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(memory_row, text="MB").grid(row=0, column=1, padx=(6, 0))

        ttk.Label(settings, text="Java 路径").grid(row=1, column=0, sticky="w", pady=3)
        java_row = ttk.Frame(settings)
        java_row.grid(row=1, column=1, sticky="ew", pady=3)
        java_row.columnconfigure(0, weight=1)
        self.java_var = tk.StringVar()
        self.java_combo = ttk.Combobox(
            java_row,
            textvariable=self.java_var,
            values=[self.AUTO_JAVA, *self.app.java_choices()],
        )
        self.java_combo.grid(row=0, column=0, sticky="ew")
        self.java_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_java_hint())
        ttk.Button(java_row, text="…", width=3, command=self._browse_java).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Button(java_row, text="检测", width=6, command=self._detect_java).grid(
            row=0, column=2, padx=(4, 0)
        )

        self.java_hint_var = tk.StringVar(value="")
        self.java_hint_label = ttk.Label(
            settings, textvariable=self.java_hint_var, foreground="#666", wraplength=360
        )
        self.java_hint_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))

        ttk.Label(settings, text="窗口尺寸").grid(row=2, column=0, sticky="w", pady=3)
        size_row = ttk.Frame(settings)
        size_row.grid(row=2, column=1, sticky="ew", pady=3)
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        ttk.Entry(size_row, textvariable=self.width_var, width=7).grid(row=0, column=0)
        ttk.Label(size_row, text="×").grid(row=0, column=1, padx=4)
        ttk.Entry(size_row, textvariable=self.height_var, width=7).grid(row=0, column=2)

        self.log_window_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            settings, text="启动时打开独立日志窗口", variable=self.log_window_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))

        launch_panel = ttk.LabelFrame(body, text="启动", padding=10)
        launch_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(8, 0))
        launch_panel.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(
            launch_panel, text="▶ 启动游戏", style="Accent.TButton", command=self._start
        )
        self.start_button.grid(row=0, column=0, sticky="ew", ipady=6)

        open_panel = ttk.LabelFrame(body, text="打开", padding=10)
        open_panel.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(8, 0))
        open_panel.columnconfigure(0, weight=1)
        ttk.Button(open_panel, text="打开日志窗口", command=self._open_log).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(open_panel, text="打开版本目录", command=self._open_dir).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )

        install_panel = ttk.LabelFrame(body, text="自动安装", padding=10)
        install_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for column in range(3):
            install_panel.columnconfigure(column, weight=1)
        self.fabric_button = ttk.Button(
            install_panel, text="装 Fabric", command=lambda: self._install_loader("fabric")
        )
        self.fabric_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.forge_button = ttk.Button(
            install_panel, text="装 Forge", command=lambda: self._install_loader("forge")
        )
        self.forge_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.neoforge_button = ttk.Button(
            install_panel, text="装 NeoForge", command=lambda: self._install_loader("neoforge")
        )
        self.neoforge_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        self.uninstall_loader_button = ttk.Button(
            install_panel, text="卸载加载器", command=self._uninstall_loader
        )
        self.uninstall_loader_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.repair_button = ttk.Button(
            install_panel, text="补全文件", command=self._repair
        )
        self.repair_button.grid(row=1, column=2, sticky="ew", pady=(6, 0), padx=(4, 0))

        # 所有设置项都是"改完立即保存"，不再有保存按钮
        self._loading = False
        self._save_job = None
        for variable in (self.memory_var, self.java_var, self.width_var, self.height_var):
            variable.trace_add("write", lambda *_: self._schedule_save())
        self.java_var.trace_add("write", lambda *_: self._update_java_hint())
        self.log_window_var.trace_add("write", lambda *_: self._schedule_save())

    # ---------------------------------------------------------------- 二级页动作

    def show_list(self) -> None:
        self.detail_frame.grid_remove()
        self.list_frame.grid()
        self.current = None
        self.refresh()

    def open(self, version_id: str) -> None:
        try:
            version = self.app.layout.load_json(version_id)
        except VersionJsonError as exc:
            messagebox.showerror("读不了版本", str(exc))
            return
        self.current = version_id
        self._loading = True
        settings = self.app.config.instance(version_id)
        memory = settings.get("max_memory_mb")
        self.memory_var.set("" if memory in (None, "") else str(memory))
        java_path = str(settings.get("java_path") or "")
        self._required_java = version.java_version
        self.java_var.set(java_path or self.AUTO_JAVA)
        self.width_var.set("" if settings.get("width") in (None, "") else str(settings["width"]))
        self.height_var.set("" if settings.get("height") in (None, "") else str(settings["height"]))
        self.log_window_var.set(bool(settings.get("show_log_window", True)))

        self.title_var.set(version_id)
        base = self.app.base_mc_version(version_id)
        self.info_vars["mainClass"].set(version.main_class or "?")
        self.info_vars["实例"].set(version_id)
        self.info_vars["版本"].set(base)
        self.info_vars["assets"].set(
            version.asset_index.id if version.asset_index else (version.assets or "?")
        )
        self.info_vars["库"].set(str(len(version.libraries)))
        self.info_vars["隔离目录"].set(str(self.app.layout.version_dir(version_id)))
        self.detail_status.set(
            f"需要 Java {version.java_version}" if version.java_version else "未标注 Java 版本"
        )
        # 加载器是"装进现有版本"的：装过就禁用安装按钮，任何一个都可以卸载还原
        from gelauncher.core.install_loader import LOADER_LABELS, loader_of

        current_loader = loader_of(self.app.layout, version_id)
        for kind, button in (
            ("fabric", self.fabric_button),
            ("forge", self.forge_button),
            ("neoforge", self.neoforge_button),
        ):
            label = LOADER_LABELS[kind]
            installed = current_loader == kind
            blocked = current_loader is not None and not installed
            button.configure(
                state="disabled" if installed or blocked else "normal",
                text=f"已装 {label}" if installed else f"装 {label}",
            )
        self.uninstall_loader_button.configure(state="normal" if current_loader else "disabled")
        self.refresh_java_choices()
        self._refresh_running_state()
        self._loading = False

        self.list_frame.grid_remove()
        self.detail_frame.grid()

    # ---------------------------------------------------------------- 即时保存

    def _schedule_save(self) -> None:
        """改一下就存；用 400ms 防抖，避免每敲一个字符写一次文件。"""
        if getattr(self, "_loading", False) or not self.current:
            return
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.after(400, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        version_id = self.current
        if not version_id:
            return
        text = self.memory_var.get().strip()
        memory: int | None = None
        if text:
            try:
                memory = int(text)
            except ValueError:
                return  # 还在输入中，先别存
        self.app.config.update_instance(
            version_id,
            max_memory_mb=memory,
            java_path=(
                ""
                if self.java_var.get().strip() == self.AUTO_JAVA
                else self.java_var.get().strip()
            ),
            width=self._int_or_none(self.width_var.get()),
            height=self._int_or_none(self.height_var.get()),
            show_log_window=bool(self.log_window_var.get()),
        )
        self.app.config.save()

    def save_settings(self) -> None:
        """兼容旧调用点：立即把当前值落盘。"""
        self._save_now()

    @staticmethod
    def _int_or_none(text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _browse_java(self) -> None:
        path = filedialog.askopenfilename(
            title="选 java.exe",
            filetypes=[("java", "java.exe"), ("可执行文件", "*.exe"), ("全部", "*.*")],
        )
        if not path:
            return
        self.java_var.set(path)
        self.app.add_java_path(path)
        self.refresh_java_choices()

    def _detect_java(self) -> None:
        self.app.detect_java()

    def refresh_java_choices(self) -> None:
        self.java_combo.configure(values=[self.AUTO_JAVA, *self.app.java_choices()])
        self._update_java_hint()

    def _update_java_hint(self) -> None:
        """在选择框下面显示"自动会选哪个 Java"，不满足版本要求时标红。"""
        from gelauncher.core.java import choose_java, probe_java_cached

        required = getattr(self, "_required_java", None)
        value = self.java_var.get().strip()
        color = "#666"
        if not value or value == self.AUTO_JAVA:
            runtime, satisfied = choose_java(
                required, self.app.java_choices(), preferred=self.app.config.java_path
            )
            if runtime is None:
                text = "自动选择：没检测到可用的 Java，点「检测」或手动指定"
                color = "#b00020"
            elif satisfied:
                text = f"自动选择：Java {runtime.major}（{runtime.path}）"
            else:
                text = (
                    f"自动选择：没找到 Java {required}，会退回 Java {runtime.major}"
                    f"（{runtime.path}）；建议装 Java {required}"
                )
                color = "#b00020"
        else:
            runtime = probe_java_cached(value)
            if runtime is None:
                text = f"手动指定：{value}（跑不起来）"
                color = "#b00020"
            elif required is not None and runtime.major < required:
                text = f"手动指定：Java {runtime.major} 低于该版本要求的 {required}，可能启动失败"
                color = "#b00020"
            else:
                text = f"手动指定：Java {runtime.major}（{runtime.path}）"
        self.java_hint_var.set(text)
        try:
            self.java_hint_label.configure(foreground=color)
        except tk.TclError:
            pass

    def _start(self) -> None:
        if self.current:
            if self._save_job is not None:
                try:
                    self.after_cancel(self._save_job)
                except tk.TclError:
                    pass
                self._save_job = None
            self._save_now()
            self.app.launch(self.current)

    def _open_log(self) -> None:
        if self.current:
            self.app.open_log_window(self.current)

    def _open_dir(self) -> None:
        import os

        path = self.app.layout.version_dir(self.current or "")
        if path.is_dir():
            os.startfile(str(path))  # noqa: S606
        else:
            messagebox.showwarning("目录不存在", str(path))

    def _install_loader(self, kind: str) -> None:
        from gelauncher.ui.loader_dialog import LoaderInstallDialog

        if self.current:
            LoaderInstallDialog(
                self,
                self.app,
                self.current,
                self.app.base_mc_version(self.current),
                kind,
                self.app.install_loader,
            )

    def _uninstall_loader(self) -> None:
        if self.current:
            self.app.detach_loader(self.current)

    def _repair(self) -> None:
        if self.current:
            self.app.repair_instance(self.current)

    def _delete_version(self) -> None:
        if self.current:
            self.app.delete_version(self.current)

    def _refresh_running_state(self) -> None:
        running = bool(self.current and self.app.is_running(self.current))
        self.start_button.configure(
            state="disabled" if running else "normal",
            text="● 正在运行" if running else "▶ 启动游戏",
        )

    def on_show(self) -> None:
        self.refresh_dirs()
        self.refresh()


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #


class AccountsPage(Page):
    title = "账号"

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._entries: dict[str, dict] = {}

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="账号", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.current_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.current_var, foreground="#666").grid(
            row=0, column=1, sticky="e"
        )

        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=230)
        body.rowconfigure(0, weight=1)

        tree_frame = ttk.LabelFrame(body, text="已添加的账号（● 表示当前）", padding=6)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame, columns=("type", "uuid"), show="tree headings", selectmode="browse"
        )
        self.tree.heading("#0", text="账号")
        self.tree.heading("type", text="类型")
        self.tree.heading("uuid", text="UUID")
        self.tree.column("#0", width=180, stretch=True)
        self.tree.column("type", width=70, anchor="center", stretch=False)
        self.tree.column("uuid", width=280)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self._on_double_click)

        actions = ttk.LabelFrame(body, text="操作", padding=10)
        actions.grid(row=0, column=1, sticky="nsew")
        actions.columnconfigure(0, weight=1)

        self.add_button = ttk.Menubutton(actions, text="添加账号 ▾")
        self.add_menu = tk.Menu(self.add_button, tearoff=False)
        self.add_menu.add_command(label="微软账号…", command=self.add_microsoft)
        self.add_menu.add_command(label="离线账号…", command=self.add_offline)
        self.add_button["menu"] = self.add_menu
        self.add_button.grid(row=0, column=0, sticky="ew")

        ttk.Button(actions, text="设为当前账号", command=self.use_selected).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(actions, text="删除选中账号", command=self.remove_selected).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )

    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._entries.clear()

        config = self.app.config
        current = config.current_account()
        for entry in config.account_entries():
            key = f"{entry['kind']}|{entry['name']}"
            self._entries[key] = entry
            is_current = (
                entry["kind"] == current["kind"] and entry["name"] == current["name"]
            )
            uuid_text = entry["uuid"] or "（离线 UUID 由名字算出）"
            self.tree.insert(
                "",
                "end",
                iid=key,
                text=("● " if is_current else "") + entry["name"],
                values=(entry["detail"], uuid_text),
            )
        config_kind = "微软账号" if current["kind"] == "msa" else "离线账号"
        self.current_var.set(f"当前：{current['name']}（{config_kind}）")
        for key, entry in self._entries.items():
            if entry["kind"] == current["kind"] and entry["name"] == current["name"]:
                self.tree.selection_set(key)
                self.tree.see(key)

    def _selected_entry(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entries.get(selection[0])

    def _on_double_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) in ("tree", "cell"):
            self.use_selected()

    # ------------------------------------------------------------------ 添加

    def add_offline(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("离线账号", "玩家名（游戏里显示的名字）：", parent=self)
        if not name:
            return
        name = name.strip()
        if not self.app.config.add_account(name):
            messagebox.showinfo("重复了", f"离线账号 {name} 已经在列表里")
            return
        self.app.config.save()
        self.refresh()
        self.app.set_status(f"已添加离线账号 {name}，并设为当前账号")

    def add_microsoft(self) -> None:
        from gelauncher.ui.msa_login import login_microsoft

        record = login_microsoft(
            self,
            self.app.config.msa_client_id,
            self.app.config.msa_authority,
        )
        if not record:
            return
        self.app.config.add_msa_account(record)
        if record.get("authority"):
            self.app.config.msa_authority = record["authority"]
        self.app.config.save()
        self.refresh()
        self.app.set_status(f"已登录微软账号 {record.get('name')}")

    # ------------------------------------------------------------------ 选择 / 删除

    def use_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if not self.app.config.select_account(entry["name"], entry["kind"]):
            return
        self.app.config.save()
        self.refresh()
        self.app.set_status(f"当前账号：{entry['name']}")

    def remove_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        name = entry["name"]
        kind = entry["kind"]
        label = "微软账号" if kind == "msa" else "离线账号"
        if not messagebox.askyesno("删除账号", f"删掉{label} {name}？"):
            return
        if kind == "msa":
            self.app.config.remove_msa_account(name)
        else:
            self.app.config.remove_account(name)
        self.app.config.save()
        self.refresh()
        self.app.set_status(f"已删除{label} {name}")


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #


class SettingsPage(Page):
    title = "设置"

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, app)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        general = ttk.LabelFrame(body, text="默认值（版本没单独设置时用）", padding=10)
        general.grid(row=0, column=0, sticky="ew")
        general.columnconfigure(1, weight=1)

        ttk.Label(general, text="最大内存").grid(row=0, column=0, sticky="w", pady=3)
        row = ttk.Frame(general)
        row.grid(row=0, column=1, sticky="ew", pady=3)
        self.memory_var = tk.StringVar()
        ttk.Spinbox(row, from_=512, to=32768, increment=512, textvariable=self.memory_var, width=10).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(row, text="MB").grid(row=0, column=1, padx=(6, 0))

        ttk.Label(general, text="Java 路径").grid(row=1, column=0, sticky="w", pady=3)
        java_row = ttk.Frame(general)
        java_row.grid(row=1, column=1, sticky="ew", pady=3)
        java_row.columnconfigure(0, weight=1)
        self.java_var = tk.StringVar()
        self.java_combo = ttk.Combobox(
            java_row, textvariable=self.java_var, values=self.app.java_choices()
        )
        self.java_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(java_row, text="…", width=3, command=self.browse_java).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Button(java_row, text="检测", width=6, command=self.detect_java).grid(
            row=0, column=2, padx=(4, 0)
        )

        self.log_window_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            general, text="默认在启动时打开独立日志窗口", variable=self.log_window_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(general, text="下载源").grid(row=5, column=0, sticky="w", pady=3)
        from gelauncher.core.net import SOURCE_MODE_LABELS, SOURCE_MODES

        self.source_labels = {SOURCE_MODE_LABELS[mode]: mode for mode in SOURCE_MODES}
        self.source_var = tk.StringVar()
        ttk.Combobox(
            general,
            textvariable=self.source_var,
            state="readonly",
            values=list(self.source_labels),
        ).grid(row=5, column=1, sticky="ew", pady=3)

        env = ttk.LabelFrame(body, text="环境", padding=10)
        env.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        env.columnconfigure(1, weight=1)
        self.env_var = tk.StringVar(value="")
        ttk.Label(env, textvariable=self.env_var, wraplength=760).grid(row=0, column=0, sticky="w")
        self.java_list_var = tk.StringVar(value="")
        ttk.Label(env, textvariable=self.java_list_var, foreground="#666", wraplength=760).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

        ttk.Label(body, text=f"Ge 启动器 {__version__}", foreground="#666").grid(
            row=3, column=0, sticky="w", pady=(14, 0)
        )

        # 即时保存：改任何一项都自动落盘
        self._loading = False
        self._save_job = None
        for variable in (self.memory_var, self.java_var):
            variable.trace_add("write", lambda *_: self._schedule_save())
        self.log_window_var.trace_add("write", lambda *_: self._schedule_save())
        self.source_var.trace_add("write", lambda *_: self._schedule_save())

    def on_show(self) -> None:
        config = self.app.config
        self._loading = True
        self.memory_var.set(str(config.max_memory_mb))
        self.java_var.set(config.java_path)
        self.log_window_var.set(bool(config.show_log_window))
        label = next(
            (text for text, mode in self.source_labels.items() if mode == config.download_source),
            next(iter(self.source_labels)),
        )
        self.source_var.set(label)
        self._loading = False
        self.refresh_java_choices()
        env = current_env()
        self.env_var.set(f"系统：{env.os_name} / {env.arch}   游戏目录：{self.app.layout.root}")

    def browse_java(self) -> None:
        path = filedialog.askopenfilename(
            title="选 java.exe", filetypes=[("java", "java.exe"), ("全部", "*.*")]
        )
        if not path:
            return
        self.java_var.set(path)
        self.app.add_java_path(path)
        self.refresh_java_choices()

    def detect_java(self) -> None:
        self.app.detect_java()

    def refresh_java_choices(self) -> None:
        self.java_combo.configure(values=self.app.java_choices())
        runtimes = getattr(self.app, "detected_javas", [])
        if runtimes:
            self.java_list_var.set(
                "；".join(f"Java {major}（{path}）" for major, path in runtimes)
            )

    def _schedule_save(self) -> None:
        if getattr(self, "_loading", False):
            return
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.after(400, self.save)

    def save(self) -> None:
        """全局默认值：改了立刻存，没有保存按钮。"""
        self._save_job = None
        config = self.app.config
        text = self.memory_var.get().strip()
        if text:
            try:
                config.max_memory_mb = int(text)
            except ValueError:
                return  # 正在输入，先别存
        if self.java_var.get().strip() or config.java_path:
            config.java_path = self.java_var.get().strip()
        config.show_log_window = bool(self.log_window_var.get())
        config.download_source = self.source_labels.get(self.source_var.get(), "auto")
        config.save()
