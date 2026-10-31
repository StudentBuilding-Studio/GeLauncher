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
        ttk.Label(header, text="过滤").grid(row=0, column=2, padx=(0, 4))
        entry = ttk.Entry(header, textvariable=self.filter_var, width=18)
        entry.grid(row=0, column=3)
        entry.bind("<KeyRelease>", lambda _e: self.refresh())
        ttk.Button(header, text="安装原版…", width=11, command=self._install_vanilla).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Button(header, text="刷新", width=8, command=self.refresh).grid(row=0, column=5, padx=(6, 0))

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
        versions.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            versions, columns=("loader", "java"), show="tree headings", selectmode="browse"
        )
        self.tree.heading("#0", text="版本")
        self.tree.heading("loader", text="加载器")
        self.tree.heading("java", text="Java")
        self.tree.column("#0", width=300, stretch=True)
        self.tree.column("loader", width=90, anchor="center", stretch=False)
        self.tree.column("java", width=70, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(versions, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<ButtonRelease-1>", self._on_click)

        ttk.Label(
            self.list_frame, text="单击版本进入设置页；左侧目录列表可以切换/添加游戏目录", foreground="#666"
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_dir_list(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="目录列表", padding=6)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.dir_tree = ttk.Treeview(frame, show="tree", selectmode="browse")
        self.dir_tree.column("#0", width=200, stretch=True)
        self.dir_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.dir_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.dir_tree.configure(yscrollcommand=scroll.set)
        self.dir_tree.bind("<<TreeviewSelect>>", self._on_dir_selected)

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
        row = self.tree.identify_row(event.y)
        region = self.tree.identify_region(event.x, event.y)
        if row and region in ("tree", "cell"):
            self.open(row)

    def _install_vanilla(self) -> None:
        from tkinter import simpledialog

        version_id = simpledialog.askstring(
            "安装原版",
            "要装的版本 id（例如 1.21.8、1.21.4、1.20.1）：",
            parent=self,
            initialvalue="1.21.8",
        )
        if version_id:
            self.app.install_vanilla(version_id.strip())

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
                    "", "end", iid=version_id, text=f"{version_id}（JSON 有问题）", values=("?", "?")
                )
                self.app.notify(f"{version_id} 的 JSON 有问题：{exc}")
                continue
            self.tree.insert(
                "",
                "end",
                iid=version_id,
                text=version_id,
                values=(detect_loader(version), version.java_version or "?"),
            )
            count += 1
        env = current_env()
        self.app.set_status(
            f"{layout.root} · {env.os_name}/{env.arch} · 共 {count} 个版本"
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
            for key in ("mainClass", "原版", "assets", "库", "隔离目录")
        }
        for index, (label, key) in enumerate(
            (
                ("主类", "mainClass"),
                ("原版", "原版"),
                ("资源索引", "assets"),
                ("库数量", "库"),
                ("游戏目录", "隔离目录"),
            )
        ):
            ttk.Label(info, text=label, width=9).grid(row=index, column=0, sticky="nw", pady=2)
            ttk.Label(info, textvariable=self.info_vars[key], wraplength=360).grid(
                row=index, column=1, sticky="w", pady=2
            )

        settings = ttk.LabelFrame(body, text="版本设置", padding=10)
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
            java_row, textvariable=self.java_var, values=self.app.java_choices()
        )
        self.java_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(java_row, text="…", width=3, command=self._browse_java).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Button(java_row, text="检测", width=6, command=self._detect_java).grid(
            row=0, column=2, padx=(4, 0)
        )

        ttk.Label(settings, text="窗口尺寸").grid(row=2, column=0, sticky="w", pady=3)
        size_row = ttk.Frame(settings)
        size_row.grid(row=2, column=1, sticky="ew", pady=3)
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        ttk.Entry(size_row, textvariable=self.width_var, width=7).grid(row=0, column=0)
        ttk.Label(size_row, text="×").grid(row=0, column=1, padx=4)
        ttk.Entry(size_row, textvariable=self.height_var, width=7).grid(row=0, column=2)
        ttk.Label(size_row, text="留空用游戏默认", foreground="#666").grid(
            row=0, column=3, padx=(8, 0)
        )

        self.log_window_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            settings, text="启动时打开独立日志窗口", variable=self.log_window_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Button(settings, text="保存设置", command=self.save_settings).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        ttk.Separator(settings).grid(row=5, column=0, columnspan=2, sticky="ew", pady=10)

        buttons = ttk.Frame(settings)
        buttons.grid(row=6, column=0, columnspan=2, sticky="nsew")
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(
            buttons, text="▶ 启动", style="Accent.TButton", command=self._start
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(
            buttons, text="■ 结束", command=self._stop, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(buttons, text="打开日志窗口", command=self._open_log).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(buttons, text="打开版本目录", command=self._open_dir).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(buttons, text="打印启动命令", command=self._print_command).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        self.fabric_button = ttk.Button(
            buttons, text="给这个版本装 Fabric", command=lambda: self._install_loader("fabric")
        )
        self.fabric_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.forge_button = ttk.Button(
            buttons, text="给这个版本装 Forge", command=lambda: self._install_loader("forge")
        )
        self.forge_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))

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
        settings = self.app.config.instance(version_id)
        memory = settings.get("max_memory_mb")
        self.memory_var.set("" if memory in (None, "") else str(memory))
        self.java_var.set(str(settings.get("java_path") or ""))
        self.width_var.set("" if settings.get("width") in (None, "") else str(settings["width"]))
        self.height_var.set("" if settings.get("height") in (None, "") else str(settings["height"]))
        self.log_window_var.set(bool(settings.get("show_log_window", True)))

        self.title_var.set(version_id)
        base = self.app.base_mc_version(version_id)
        self.info_vars["mainClass"].set(version.main_class or "?")
        self.info_vars["原版"].set(base)
        self.info_vars["assets"].set(
            version.asset_index.id if version.asset_index else (version.assets or "?")
        )
        self.info_vars["库"].set(str(len(version.libraries)))
        self.info_vars["隔离目录"].set(str(self.app.layout.version_dir(version_id)))
        self.detail_status.set(f"Java {version.java_version or '?'}")
        # 已经是某个加载器的版本：对应按钮就没意义了（点了只会原地重装）
        is_fabric = version_id.startswith("fabric-loader-")
        is_forge = "-forge-" in version_id
        self.fabric_button.configure(
            state="disabled" if is_fabric else "normal",
            text="这已经是 Fabric 版本" if is_fabric else "给这个版本装 Fabric",
        )
        self.forge_button.configure(
            state="disabled" if is_forge else "normal",
            text="这已经是 Forge 版本" if is_forge else "给这个版本装 Forge",
        )
        self.refresh_java_choices()
        self._refresh_running_state()

        self.list_frame.grid_remove()
        self.detail_frame.grid()

    def save_settings(self) -> None:
        if not self.current:
            return
        version_id = self.current
        try:
            memory = int(self.memory_var.get()) if self.memory_var.get().strip() else None
        except ValueError:
            messagebox.showerror("内存填错了", "最大内存要填整数（MB）")
            return
        width = self._int_or_none(self.width_var.get())
        height = self._int_or_none(self.height_var.get())
        self.app.config.update_instance(
            version_id,
            max_memory_mb=memory,
            java_path=self.java_var.get().strip(),
            width=width,
            height=height,
            show_log_window=bool(self.log_window_var.get()),
        )
        self.app.config.save()
        self.app.set_status(f"{version_id} 的设置已保存")

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
        self.java_combo.configure(values=self.app.java_choices())

    def _start(self) -> None:
        if self.current:
            self.save_settings()
            self.app.launch(self.current)

    def _stop(self) -> None:
        if self.current:
            self.app.stop(self.current)

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

    def _print_command(self) -> None:
        if self.current:
            self.app.print_command(self.current)

    def _install_loader(self, kind: str) -> None:
        if self.current:
            self.app.install_loader(self.app.base_mc_version(self.current), kind)

    def _refresh_running_state(self) -> None:
        running = bool(self.current and self.app.is_running(self.current))
        self.stop_button.configure(state="normal" if running else "disabled")

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

        ttk.Separator(actions).grid(row=3, column=0, sticky="ew", pady=12)
        ttk.Label(
            actions,
            text=(
                "微软账号：登录后可以联机、显示正版皮肤。\n"
                "离线账号：只在本地用，名字和 UUID 自己定。\n\n"
                "微软登录需要在 Azure 注册一个应用，\n"
                "步骤见 docs/msa-setup.md。"
            ),
            foreground="#666",
            justify="left",
            wraplength=210,
        ).grid(row=4, column=0, sticky="w")

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

        game = ttk.LabelFrame(body, text="游戏目录", padding=10)
        game.grid(row=0, column=0, sticky="ew")
        game.columnconfigure(1, weight=1)
        self.game_dir_var = tk.StringVar()
        ttk.Entry(game, textvariable=self.game_dir_var, state="readonly").grid(
            row=0, column=0, columnspan=3, sticky="ew"
        )
        ttk.Button(game, text="切换…", command=self.choose_game_dir).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(game, text="打开", command=self.open_game_dir).grid(row=1, column=1, sticky="w", pady=(8, 0))

        general = ttk.LabelFrame(body, text="默认值（版本没单独设置时用）", padding=10)
        general.grid(row=1, column=0, sticky="ew", pady=(10, 0))
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

        ttk.Label(general, text="微软 client id").grid(row=3, column=0, sticky="w", pady=3)
        self.client_id_var = tk.StringVar()
        ttk.Entry(general, textvariable=self.client_id_var).grid(
            row=3, column=1, sticky="ew", pady=3
        )
        ttk.Label(
            general,
            text="用微软账号登录前要填这个，注册步骤见 docs/msa-setup.md",
            foreground="#666",
            wraplength=520,
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Button(body, text="保存设置", command=self.save).grid(row=2, column=0, sticky="ew", pady=(10, 0))

        env = ttk.LabelFrame(body, text="环境", padding=10)
        env.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        env.columnconfigure(1, weight=1)
        self.env_var = tk.StringVar(value="")
        ttk.Label(env, textvariable=self.env_var, wraplength=760).grid(row=0, column=0, sticky="w")
        ttk.Button(env, text="检测本机 Java", command=self.detect_java).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.java_list_var = tk.StringVar(value="")
        ttk.Label(env, textvariable=self.java_list_var, foreground="#666", wraplength=760).grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )

        ttk.Label(body, text=f"Ge 启动器 {__version__}", foreground="#666").grid(
            row=4, column=0, sticky="w", pady=(14, 0)
        )

    def on_show(self) -> None:
        config = self.app.config
        self.game_dir_var.set(str(self.app.layout.root))
        self.memory_var.set(str(config.max_memory_mb))
        self.java_var.set(config.java_path)
        self.log_window_var.set(bool(config.show_log_window))
        self.client_id_var.set(config.msa_client_id)
        self.refresh_java_choices()
        env = current_env()
        self.env_var.set(f"系统：{env.os_name} / {env.arch}   游戏目录：{self.app.layout.root}")

    def choose_game_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.app.layout.root), title="选 .minecraft 目录")
        if not chosen:
            return
        self.app.change_game_dir(chosen)

    def open_game_dir(self) -> None:
        import os

        if self.app.layout.root.is_dir():
            os.startfile(str(self.app.layout.root))  # noqa: S606

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

    def save(self) -> None:
        config = self.app.config
        try:
            config.max_memory_mb = int(self.memory_var.get())
        except ValueError:
            messagebox.showerror("内存填错了", "最大内存要填整数（MB）")
            return
        config.java_path = self.java_var.get().strip()
        config.show_log_window = bool(self.log_window_var.get())
        config.msa_client_id = self.client_id_var.get().strip()
        config.save()
        self.app.set_status("设置已保存")
