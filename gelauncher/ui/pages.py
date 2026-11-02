"""主窗口里的三个一级页面：版本、账号、设置。

版本页是"列表 → 单击进入二级页"的结构（参考 HMCL）：二级页里放这个版本自己的设置，
包括是否启用独立日志窗口。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

from gelauncher.ui import theme

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


class Page(ctk.CTkFrame):
    """页面基类：统一背景和"切到本页时刷新"的入口。"""

    title = "页面"

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, fg_color=theme.BG, corner_radius=0)
        self.app = app

    def on_show(self) -> None:  # pragma: no cover - 子类按需覆盖
        pass


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

        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.detail_frame = ctk.CTkFrame(self, fg_color="transparent")
        for frame in (self.list_frame, self.detail_frame):
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)

        self._build_list()
        self._build_detail()
        self.show_list()

    # ---------------------------------------------------------------- 列表

    def _build_list(self) -> None:
        from gelauncher.ui.instance_list import InstanceList
        from gelauncher.ui.widgets import Card, button, label

        header = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text="版本",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.dir_hint_var = tk.StringVar(value="")
        label(header, muted=True, textvariable=self.dir_hint_var).grid(
            row=0, column=1, sticky="w", padx=12
        )

        body = tk.PanedWindow(
            self.list_frame,
            orient="horizontal",
            sashwidth=6,
            sashrelief="flat",
            bd=0,
            bg=theme.bg(),
            opaqueresize=False,
        )
        body.grid(row=1, column=0, sticky="nsew")
        self.list_frame.rowconfigure(1, weight=1)
        self._paned = body

        scale = getattr(self.app, "scale", 1.0)
        dir_card = self._build_dir_list(body)
        versions = Card(body, "已安装的版本")
        body.add(dir_card, minsize=int(190 * scale))
        body.add(versions, minsize=int(340 * scale))
        self._pane_ready = False
        self._pane_job = None
        body.bind("<Configure>", self._on_pane_configure)

        # 过滤 / 安装原版 / 导入整合包 / 刷新
        toolbar = ctk.CTkFrame(versions.body, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        label(toolbar, "过滤").grid(row=0, column=0, padx=(0, 6))
        filter_entry = ctk.CTkEntry(toolbar, textvariable=self.filter_var, width=180)
        filter_entry.grid(row=0, column=1, sticky="w")
        filter_entry.bind("<KeyRelease>", lambda _e: self.refresh())
        button(toolbar, "安装原版…", self._install_vanilla).grid(
            row=0, column=2, padx=(10, 0)
        )
        button(toolbar, "导入整合包…", self._import_modpack).grid(
            row=0, column=3, padx=(6, 0)
        )
        button(
            toolbar,
            "↻",
            self.refresh,
            width=38,
            height=32,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=18),
        ).grid(row=0, column=4, padx=(6, 0))

        versions.body.columnconfigure(0, weight=1)
        versions.body.rowconfigure(1, weight=1)
        self.tree = InstanceList(
            versions.body,
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
        self.tree.grid(row=1, column=0, sticky="nsew")

    def _on_pane_configure(self, _event=None) -> None:
        """窗口尺寸稳定 250ms 后再定初始分隔位置。"""
        if getattr(self, "_pane_ready", False):
            return
        if self._pane_job is not None:
            try:
                self.after_cancel(self._pane_job)
            except tk.TclError:
                pass
        self._pane_job = self.after(250, self._init_pane)

    def _init_pane(self) -> None:
        """目录列表默认占约 22%，之后用户可以拖动分隔条自己调。"""
        self._pane_job = None
        if getattr(self, "_pane_ready", False):
            return
        try:
            width = self._paned.winfo_width()
            if width <= 100:
                return
            target = max(int(width * 0.22), int(180 * getattr(self.app, "scale", 1.0)))
            self._paned.sash_place(0, min(target, int(width * 0.5)), 0)
            self._pane_ready = True
        except tk.TclError:
            pass

    def _build_dir_list(self, parent):
        from gelauncher.ui.instance_list import InstanceList
        from gelauncher.ui.widgets import Card, button

        card = Card(parent, "目录列表")
        card.body.columnconfigure(0, weight=1)
        card.body.rowconfigure(0, weight=1)

        self.dir_tree = InstanceList(
            card.body,
            headers=("游戏目录",),
            column_widths=(int(230 * getattr(self.app, "scale", 1.0)),),
            on_row=self._on_dir_row,
        )
        self.dir_tree.grid(row=0, column=0, sticky="nsew")

        buttons = ctk.CTkFrame(card.body, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        button(buttons, "添加目录…", self._add_dir).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        button(buttons, "打开游戏目录", self._open_selected_dir).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        button(card.body, "从列表移除", self._remove_dir).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        return card

    def refresh_dirs(self) -> None:
        self.dir_tree.delete(*self.dir_tree.get_children())
        for directory in self.app.config.game_dirs:
            label = Path(directory).name or directory
            current = _same_path(directory, self.app.layout.root)
            self.dir_tree.insert(
                "",
                "end",
                iid=directory,
                text=label,
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

    def _open_selected_dir(self) -> None:
        """打开目录列表里选中的游戏目录（没选中就打开当前目录）。"""
        import os

        selection = self.dir_tree.selection()
        path = selection[0] if selection else str(self.app.layout.root)
        if not Path(path).is_dir():
            messagebox.showwarning("目录不存在", path)
            return
        try:
            os.startfile(path)  # noqa: S606
        except OSError as exc:
            messagebox.showerror("打不开目录", f"{path}\n{exc}")

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

    def refresh(self, select: str | None = None, force: bool = True) -> None:
        layout = self.app.layout
        fingerprint = (str(layout.root), tuple(layout.installed_versions()), self.filter_var.get())
        if not force and fingerprint == getattr(self, "_fingerprint", None):
            return
        self._fingerprint = fingerprint
        self.tree.delete(*self.tree.get_children())
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

    def _section_row(self, parent, title: str, subtitle: str = "", control_factory=None):
        from gelauncher.ui.widgets import settings_row

        return settings_row(parent, title, subtitle, control_factory)

    def _build_detail(self) -> None:
        from gelauncher.ui.widgets import Card, Combo, button

        header = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        button(header, "← 返回", self.show_list, width=90).grid(row=0, column=0, sticky="w")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w", padx=12)
        self.title_var = tk.StringVar(value="")
        ctk.CTkLabel(
            titles,
            textvariable=self.title_var,
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=18, weight="bold"),
        ).pack(anchor="w")
        self.detail_status = tk.StringVar(value="")
        ctk.CTkLabel(
            titles,
            textvariable=self.detail_status,
            anchor="w",
            text_color=theme.MUTED,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(anchor="w", pady=(2, 0))
        self.start_button = button(header, "▶ 启动游戏", self._start, accent=True, width=140, height=40)
        self.start_button.grid(row=0, column=2, sticky="e")

        body = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.detail_frame.rowconfigure(1, weight=1)

        nav = ctk.CTkFrame(body, corner_radius=10, fg_color=theme.CARD, width=190)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        nav.grid_propagate(False)
        nav.columnconfigure(0, weight=1)
        nav.rowconfigure(6, weight=1)
        self._detail_nav_buttons: dict[str, ctk.CTkButton] = {}
        for index, (key, text) in enumerate(
            (
                ("instance", "实例设置"),
                ("install", "自动安装"),
                ("browse", "浏览"),
                ("manage", "管理"),
            ),
            start=0,
        ):
            nav_button = ctk.CTkButton(
                nav,
                text=text,
                anchor="w",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                text_color=theme.TEXT,
                hover_color=("#e2e8f0", "#2a2f37"),
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                command=lambda name=key: self._show_detail_section(name),
            )
            nav_button.grid(row=index, column=0, sticky="ew", padx=8, pady=3)
            self._detail_nav_buttons[key] = nav_button

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self._detail_sections: dict[str, ctk.CTkScrollableFrame] = {}
        for key in ("instance", "install", "browse", "manage"):
            section = ctk.CTkScrollableFrame(content, fg_color="transparent", corner_radius=0)
            section.grid(row=0, column=0, sticky="nsew")
            section.grid_remove()
            self._detail_sections[key] = section

        # ---------------- 实例设置：基本设置 ----------------
        instance = self._detail_sections["instance"]
        basic_card = Card(instance, "基本设置")
        basic_card.pack(fill="x", padx=4, pady=(0, 10))

        self.java_var = tk.StringVar()

        def _java_control(row):
            frame = ctk.CTkFrame(row, fg_color="transparent")
            self.java_combo = Combo(
                frame,
                variable=self.java_var,
                values=[self.AUTO_JAVA, *self.app.java_choices()],
                width=300,
                command=lambda _value: self._update_java_hint(),
            )
            self.java_combo.pack(side="left")
            button(frame, "…", self._browse_java, width=38, height=30).pack(
                side="left", padx=(6, 0)
            )
            button(frame, "检测", self._detect_java, width=68, height=30).pack(
                side="left", padx=(6, 0)
            )
            return frame

        self._section_row(basic_card.body, "游戏 Java", "", _java_control)
        self.java_hint_var = tk.StringVar(value="")
        self.java_hint_label = ctk.CTkLabel(
            basic_card.body,
            textvariable=self.java_hint_var,
            anchor="w",
            justify="left",
            wraplength=620,
            text_color=theme.MUTED,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
        )
        self.java_hint_label.pack(fill="x", padx=14, pady=(0, 6))

        self.memory_var = tk.StringVar()

        def _memory_control(row):
            frame = ctk.CTkFrame(row, fg_color="transparent")
            ctk.CTkEntry(frame, textvariable=self.memory_var, width=100).pack(side="left")
            button(frame, "−", lambda: self._step_memory(-512), width=32, height=30).pack(
                side="left", padx=(8, 0)
            )
            button(frame, "+", lambda: self._step_memory(512), width=32, height=30).pack(
                side="left", padx=(4, 0)
            )
            ctk.CTkLabel(
                frame,
                text="MB",
                text_color=theme.MUTED,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            ).pack(side="left", padx=(8, 0))
            return frame

        self._section_row(basic_card.body, "游戏内存", "按需分配，建议 4–8 GB", _memory_control)

        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()

        def _size_control(row):
            frame = ctk.CTkFrame(row, fg_color="transparent")
            ctk.CTkEntry(frame, textvariable=self.width_var, width=80).pack(side="left")
            ctk.CTkLabel(
                frame,
                text="×",
                text_color=theme.MUTED,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            ).pack(side="left", padx=6)
            ctk.CTkEntry(frame, textvariable=self.height_var, width=80).pack(side="left")
            return frame

        self._section_row(basic_card.body, "游戏窗口尺寸", "留空则使用游戏默认值", _size_control)

        self.log_window_var = tk.BooleanVar(value=True)
        self._section_row(
            basic_card.body,
            "启动时打开日志窗口",
            "每次启动都弹一个独立日志窗口，方便看崩溃信息",
            lambda row: ctk.CTkSwitch(row, text="", variable=self.log_window_var),
        )

        # ---------------- 高级设置 ----------------
        advanced_card = Card(instance, "高级设置")
        advanced_card.pack(fill="x", padx=4, pady=(0, 10))
        self.jvm_var = tk.StringVar()
        self._section_row(
            advanced_card.body,
            "JVM 参数",
            "追加到 java 命令里，例如 -XX:+UseG1GC -XX:MaxGCPauseMillis=50",
            lambda row: ctk.CTkEntry(
                row,
                textvariable=self.jvm_var,
                width=360,
                placeholder_text="-XX:+UseG1GC …",
                font=ctk.CTkFont(family="Consolas", size=12),
            ),
        )

        from gelauncher.core.process_priority import PRIORITY_LABELS
        from gelauncher.ui.widgets import Combo as _Combo

        self.priority_labels = dict(PRIORITY_LABELS)
        self.priority_var = tk.StringVar(value="正常")
        self._section_row(
            advanced_card.body,
            "进程优先级",
            "高优先级会让游戏更容易抢到 CPU，但可能拖慢系统其它程序",
            lambda row: _Combo(
                row,
                variable=self.priority_var,
                values=list(self.priority_labels),
                width=160,
                readonly=True,
            ),
        )

        # ---------------- 实例信息 ----------------
        info_card = Card(instance, "实例信息")
        info_card.pack(fill="x", padx=4, pady=(0, 10))
        self.info_vars = {
            key: tk.StringVar(value="-")
            for key in (
                "mainClass",
                "实例",
                "版本",
                "加载器",
                "assets",
                "库",
                "隔离目录",
            )
        }
        for text, key in (
            ("实例名", "实例"),
            ("游戏版本", "版本"),
            ("加载器", "加载器"),
            ("资源索引", "assets"),
            ("库数量", "库"),
            ("主类", "mainClass"),
            ("游戏目录", "隔离目录"),
        ):
            self._section_row(
                info_card.body,
                text,
                control_factory=lambda row, key=key: ctk.CTkLabel(
                    row,
                    textvariable=self.info_vars[key],
                    anchor="e",
                    justify="right",
                    wraplength=420,
                    text_color=theme.MUTED,
                    font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                ),
            )

        # ---------------- 自动安装 ----------------
        install = self._detail_sections["install"]
        install_card = Card(install, "安装加载器")
        install_card.pack(fill="x", padx=4, pady=(0, 10))
        install_form = ctk.CTkFrame(install_card.body, fg_color="transparent")
        install_form.pack(fill="x", padx=14, pady=6)
        for column in range(3):
            install_form.columnconfigure(column, weight=1)
        self.fabric_button = button(
            install_form, "装 Fabric", lambda: self._install_loader("fabric")
        )
        self.fabric_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.forge_button = button(
            install_form, "装 Forge", lambda: self._install_loader("forge")
        )
        self.forge_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.neoforge_button = button(
            install_form, "装 NeoForge", lambda: self._install_loader("neoforge")
        )
        self.neoforge_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        repair_card = Card(install, "维护")
        repair_card.pack(fill="x", padx=4, pady=(0, 10))

        def _uninstall_control(row):
            self.uninstall_loader_button = button(
                row, "卸载加载器", self._uninstall_loader, width=110
            )
            return self.uninstall_loader_button

        self._section_row(
            repair_card.body,
            "卸载加载器",
            "把实例还原成原版（保留存档和 mods）",
            _uninstall_control,
        )

        def _repair_control(row):
            self.repair_button = button(row, "补全文件", self._repair, width=110)
            return self.repair_button

        self._section_row(
            repair_card.body,
            "补全缺失文件",
            "黑屏 / 缺贴图 / 没声音时用，只补缺的文件",
            _repair_control,
        )

        # ---------------- 浏览 ----------------
        browse = self._detail_sections["browse"]
        browse_card = Card(browse, "打开目录")
        browse_card.pack(fill="x", padx=4, pady=(0, 10))
        browse_items = (
            ("日志窗口", None),
            ("版本目录", None),
            ("世界", "saves"),
            ("模组", "mods"),
            ("资源包", "resourcepacks"),
            ("光影包", "shaderpacks"),
            ("截图", "screenshots"),
        )
        for text, folder in browse_items:
            if folder is None:
                action = self._open_log if text == "日志窗口" else self._open_version_dir
            else:
                action = lambda name=folder: self._browse(name)
            self._section_row(
                browse_card.body,
                text,
                "",
                control_factory=lambda row, action=action: button(
                    row, "打开", action, width=90
                ),
            )

        # ---------------- 管理 ----------------
        manage = self._detail_sections["manage"]
        manage_card = Card(manage, "危险操作")
        manage_card.pack(fill="x", padx=4, pady=(0, 10))
        self._section_row(
            manage_card.body,
            "删除实例",
            "会删掉整个 versions/<实例> 目录，存档也会一起没，删之前先备份",
            control_factory=lambda row: button(
                row, "删除这个实例", self._delete_version, danger=True, width=140
            ),
        )

        self._show_detail_section("instance")

        # 即时保存 + Java 提示
        self._loading = False
        self._save_job = None
        for variable in (
            self.memory_var,
            self.java_var,
            self.width_var,
            self.height_var,
            self.jvm_var,
        ):
            variable.trace_add("write", lambda *_: self._schedule_save())
        self.java_var.trace_add("write", lambda *_: self._update_java_hint())
        self.priority_var.trace_add("write", lambda *_: self._schedule_save())
        self.log_window_var.trace_add("write", lambda *_: self._schedule_save())

    def _show_detail_section(self, name: str) -> None:
        if name not in self._detail_sections:
            return
        for key, section in self._detail_sections.items():
            if key == name:
                section.grid()
            else:
                section.grid_remove()
        for key, nav_button in self._detail_nav_buttons.items():
            active = key == name
            nav_button.configure(
                fg_color=theme.ACCENT if active else "transparent",
                text_color="#ffffff" if active else theme.TEXT,
            )

    def _step_memory(self, delta: int) -> None:
        try:
            current = int(self.memory_var.get().strip() or self.app.config.max_memory_mb)
        except ValueError:
            current = self.app.config.max_memory_mb
        value = max(512, min(32768, current + delta))
        self.memory_var.set(str(value))

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
        settings = self.app.config.instance(version_id, root=self.app.layout.root)
        memory = settings.get("max_memory_mb")
        self.memory_var.set("" if memory in (None, "") else str(memory))
        java_path = str(settings.get("java_path") or "")
        self._required_java = version.java_version
        self.java_var.set(java_path or self.AUTO_JAVA)
        self.width_var.set("" if settings.get("width") in (None, "") else str(settings["width"]))
        self.height_var.set("" if settings.get("height") in (None, "") else str(settings["height"]))
        self.log_window_var.set(bool(settings.get("show_log_window", True)))
        self.jvm_var.set(str(settings.get("jvm_args") or ""))
        priority_mode = str(settings.get("process_priority") or "normal")
        priority_label = next(
            (text for text, mode in self.priority_labels.items() if mode == priority_mode),
            "正常",
        )
        self.priority_var.set(priority_label)

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
        self.info_vars["加载器"].set(
            LOADER_LABELS[current_loader] if current_loader else "原版"
        )
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
            root=self.app.layout.root,
            max_memory_mb=memory,
            java_path=(
                ""
                if self.java_var.get().strip() == self.AUTO_JAVA
                else self.java_var.get().strip()
            ),
            width=self._int_or_none(self.width_var.get()),
            height=self._int_or_none(self.height_var.get()),
            show_log_window=bool(self.log_window_var.get()),
            jvm_args=self.jvm_var.get().strip(),
            process_priority=self.priority_labels.get(self.priority_var.get(), "normal"),
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

    def _refresh_java_hint_async(self) -> None:
        self._java_warm = False
        self._update_java_hint()

    def refresh_java_choices(self) -> None:
        self.java_combo.configure(values=[self.AUTO_JAVA, *self.app.java_choices()])
        self._update_java_hint()

    def _update_java_hint(self) -> None:
        """在选择框下面显示"自动会选哪个 Java"，不满足版本要求时标红。"""
        import threading

        from gelauncher.core import java as java_core

        required = getattr(self, "_required_java", None)
        value = self.java_var.get().strip()
        paths = list(self.app.java_choices())

        # 第一次点开版本时，别在 UI 线程里跑 java -version（每个要几百毫秒）
        preferred = getattr(self.app.config, "java_path", "")
        if preferred and preferred not in paths:
            paths.append(preferred)
        missing = any(
            java_core.cached_java(item) is java_core._MISSING for item in paths
        )
        if missing:
            self.java_hint_var.set("正在检测 Java…")
            if not getattr(self, "_java_warm", False):
                self._java_warm = True

                def warm() -> None:
                    for item in paths:
                        try:
                            java_core.probe_java_cached(item)
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        self.app.messages.put(("java_hint", None))
                    except Exception:  # noqa: BLE001 - 窗口已关
                        pass

                threading.Thread(target=warm, daemon=True).start()
            return

        from gelauncher.core.java import choose_java, probe_java_cached

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
            self.java_hint_label.configure(text_color=color)
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

    def _open_version_dir(self) -> None:
        self._browse(None)

    def _browse(self, folder: str | None) -> None:
        """在资源管理器里打开版本目录（或其下的世界/模组/资源包…）。"""
        import os

        if not self.current:
            return
        path = self.app.layout.version_dir(self.current)
        if folder:
            path = path / folder
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showwarning("建不了目录", f"{path}\n{exc}")
                return
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
        self.refresh(force=False)


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #


class AccountsPage(Page):
    title = "账号"

    def __init__(self, parent: tk.Misc, app) -> None:
        import customtkinter as ctk

        from gelauncher.ui.instance_list import InstanceList
        from gelauncher.ui.widgets import Card, button, label

        super().__init__(parent, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._entries: dict[str, dict] = {}

        from gelauncher.ui import theme

        header = ctk.CTkFrame(self, fg_color=theme.BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text="账号",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.current_var = tk.StringVar(value="")
        label(header, muted=True, textvariable=self.current_var).grid(
            row=0, column=1, sticky="e"
        )

        body = ctk.CTkFrame(self, fg_color=theme.BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=270)
        body.rowconfigure(0, weight=1)

        list_card = Card(body, "已添加的账号（● 表示当前）")
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        list_card.body.rowconfigure(0, weight=1)
        self.tree = InstanceList(
            list_card.body,
            headers=("账号", "类型", "UUID"),
            column_widths=(300, 90, 340),
            on_row=lambda _iid: None,
            on_double=lambda _iid: self.use_selected(),
        )
        self.tree.grid(row=0, column=0, sticky="nsew")

        actions = Card(body, "操作")
        actions.grid(row=0, column=1, sticky="nsew")
        self.add_button = button(actions.body, "添加账号 ▾", self._popup_add_menu, accent=True)
        self.add_button.grid(row=0, column=0, sticky="ew")
        button(actions.body, "设为当前账号", self.use_selected).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )
        button(actions.body, "删除选中账号", self.remove_selected, danger=True).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )

        self._add_menu = tk.Menu(self, tearoff=False)
        self._add_menu.add_command(label="微软账号…", command=self.add_microsoft)
        self._add_menu.add_command(label="离线账号…", command=self.add_offline)

    def _popup_add_menu(self) -> None:
        try:
            x = self.add_button.winfo_rootx()
            y = self.add_button.winfo_rooty() + self.add_button.winfo_height()
            self._add_menu.tk_popup(x, y)
        finally:
            try:
                self._add_menu.grab_release()
            except tk.TclError:
                pass

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

    # ------------------------------------------------------------------ 添加

    def add_offline(self) -> None:
        from gelauncher.ui.offline_dialog import ask_offline_name

        name = ask_offline_name(self)
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
        label_text = "微软账号" if kind == "msa" else "离线账号"
        if not messagebox.askyesno("删除账号", f"删掉{label_text} {name}？"):
            return
        if kind == "msa":
            self.app.config.remove_msa_account(name)
        else:
            self.app.config.remove_account(name)
        self.app.config.save()
        self.refresh()
        self.app.set_status(f"已删除{label_text} {name}")


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #


class SettingsPage(Page):
    title = "设置"

    def _row(self, parent, title: str, subtitle: str = "", control_factory=None):
        from gelauncher.ui.widgets import settings_row

        return settings_row(parent, title, subtitle, control_factory)

    def _build_shell(self) -> None:
        from gelauncher.ui.widgets import Card

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        nav = ctk.CTkFrame(body, corner_radius=10, fg_color=theme.CARD, width=170)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        nav.grid_propagate(False)
        nav.columnconfigure(0, weight=1)
        nav.rowconfigure(5, weight=1)
        self._setting_nav_buttons: dict[str, ctk.CTkButton] = {}
        for index, (key, text) in enumerate(
            (
                ("general", "常规"),
                ("appearance", "外观"),
                ("env", "环境"),
                ("about", "关于"),
            ),
            start=0,
        ):
            nav_button = ctk.CTkButton(
                nav,
                text=text,
                anchor="w",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                text_color=theme.TEXT,
                hover_color=("#e2e8f0", "#2a2f37"),
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                command=lambda name=key: self._show_section(name),
            )
            nav_button.grid(row=index, column=0, sticky="ew", padx=8, pady=3)
            self._setting_nav_buttons[key] = nav_button

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self._setting_sections: dict[str, ctk.CTkScrollableFrame] = {}
        for key in ("general", "appearance", "env", "about"):
            section = ctk.CTkScrollableFrame(content, fg_color="transparent", corner_radius=0)
            section.grid(row=0, column=0, sticky="nsew")
            section.grid_remove()
            self._setting_sections[key] = section

        # 常规
        general = self._setting_sections["general"]
        basic_card = Card(general, "基本设置（默认值）")
        basic_card.pack(fill="x", padx=4, pady=(0, 10))

        self.java_var = tk.StringVar()

        def _java_control(row):
            from gelauncher.ui.widgets import Combo, button

            frame = ctk.CTkFrame(row, fg_color="transparent")
            self.java_combo = Combo(
                frame, variable=self.java_var, values=self.app.java_choices(), width=300
            )
            self.java_combo.pack(side="left")
            button(frame, "…", self.browse_java, width=38, height=30).pack(
                side="left", padx=(6, 0)
            )
            button(frame, "检测", self.detect_java, width=68, height=30).pack(
                side="left", padx=(6, 0)
            )
            return frame

        self._row(basic_card.body, "游戏 Java", "", _java_control)

        self.memory_var = tk.StringVar()

        def _memory_control(row):
            from gelauncher.ui.widgets import button

            frame = ctk.CTkFrame(row, fg_color="transparent")
            ctk.CTkEntry(frame, textvariable=self.memory_var, width=100).pack(side="left")
            button(frame, "−", lambda: self._step_memory(-512), width=32, height=30).pack(
                side="left", padx=(8, 0)
            )
            button(frame, "+", lambda: self._step_memory(512), width=32, height=30).pack(
                side="left", padx=(4, 0)
            )
            ctk.CTkLabel(
                frame,
                text="MB",
                text_color=theme.MUTED,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            ).pack(side="left", padx=(8, 0))
            return frame

        self._row(basic_card.body, "游戏内存", "按需分配，建议 4–8 GB", _memory_control)

        self.default_width_var = tk.StringVar()
        self.default_height_var = tk.StringVar()

        def _size_control(row):
            frame = ctk.CTkFrame(row, fg_color="transparent")
            ctk.CTkEntry(frame, textvariable=self.default_width_var, width=80).pack(side="left")
            ctk.CTkLabel(
                frame,
                text="×",
                text_color=theme.MUTED,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            ).pack(side="left", padx=6)
            ctk.CTkEntry(frame, textvariable=self.default_height_var, width=80).pack(side="left")
            return frame

        self._row(basic_card.body, "游戏窗口尺寸", "留空则使用游戏默认值", _size_control)

        self.log_window_var = tk.BooleanVar(value=True)
        self._row(
            basic_card.body,
            "启动时打开日志窗口",
            "每次启动都弹一个独立日志窗口，方便看崩溃信息",
            lambda row: ctk.CTkSwitch(row, text="", variable=self.log_window_var),
        )

        advanced_card = Card(general, "高级设置（默认值）")
        advanced_card.pack(fill="x", padx=4, pady=(0, 10))

        self.default_jvm_var = tk.StringVar()
        self._row(
            advanced_card.body,
            "JVM 参数",
            "追加到 java 命令里，例如 -XX:+UseG1GC -XX:MaxGCPauseMillis=50",
            lambda row: ctk.CTkEntry(
                row,
                textvariable=self.default_jvm_var,
                width=360,
                font=ctk.CTkFont(family="Consolas", size=12),
            ),
        )

        from gelauncher.core.process_priority import PRIORITY_LABELS

        self.priority_labels = dict(PRIORITY_LABELS)
        self.default_priority_var = tk.StringVar(value="正常")

        def _priority_control(row):
            from gelauncher.ui.widgets import Combo

            return Combo(
                row,
                variable=self.default_priority_var,
                values=list(self.priority_labels),
                width=160,
                readonly=True,
            )

        self._row(
            advanced_card.body,
            "进程优先级",
            "高优先级会让游戏更容易抢到 CPU，但可能拖慢系统其它程序",
            _priority_control,
        )

        other_card = Card(general, "其它")
        other_card.pack(fill="x", padx=4, pady=(0, 10))
        from gelauncher.core.net import SOURCE_MODE_LABELS, SOURCE_MODES

        self.source_labels = {SOURCE_MODE_LABELS[mode]: mode for mode in SOURCE_MODES}
        self.source_var = tk.StringVar()

        def _source_control(row):
            from gelauncher.ui.widgets import Combo

            self.source_combo = Combo(
                row,
                variable=self.source_var,
                values=list(self.source_labels),
                width=300,
                readonly=True,
            )
            return self.source_combo

        self._row(other_card.body, "下载源", "自动 = 官方优先，慢/不通时切镜像", _source_control)

        # 外观
        appearance = self._setting_sections["appearance"]
        appearance_card = Card(appearance, "主题")
        appearance_card.pack(fill="x", padx=4, pady=(0, 10))
        from gelauncher.ui import theme as theme_module

        self.appearance_labels = dict(theme_module.MODE_LABELS)
        self.appearance_var = tk.StringVar(
            value=next(
                text
                for text, mode in self.appearance_labels.items()
                if mode == self.app.config.appearance
            )
        )

        def _appearance_control(row):
            from gelauncher.ui.widgets import Combo

            self.appearance_combo = Combo(
                row,
                variable=self.appearance_var,
                values=list(self.appearance_labels),
                width=180,
                readonly=True,
                command=self._apply_appearance,
            )
            return self.appearance_combo

        self._row(appearance_card.body, "主题色", "跟随系统会随 Windows 深色模式自动切换", _appearance_control)

        # 环境
        env = self._setting_sections["env"]
        env_card = Card(env, "环境")
        env_card.pack(fill="x", padx=4, pady=(0, 10))
        self.env_var = tk.StringVar(value="")
        self.env_label = ctk.CTkLabel(
            env_card.body,
            textvariable=self.env_var,
            anchor="w",
            justify="left",
            text_color=theme.MUTED,
            wraplength=760,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )
        self.env_label.pack(fill="x", padx=14, pady=(6, 2))
        self.java_list_var = tk.StringVar(value="")
        self.java_list_label = ctk.CTkLabel(
            env_card.body,
            textvariable=self.java_list_var,
            anchor="w",
            justify="left",
            text_color=theme.MUTED,
            wraplength=760,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )
        self.java_list_label.pack(fill="x", padx=14, pady=(2, 8))

        def _refit_env(event) -> None:
            from gelauncher.ui import theme as theme_mod

            wrap = max(int(event.width / theme_mod.scale_factor()) - 24, 260)
            self.env_label.configure(wraplength=wrap)
            self.java_list_label.configure(wraplength=wrap)

        env_card.body.bind("<Configure>", _refit_env, add="+")

        # 关于
        about = self._setting_sections["about"]
        about_card = Card(about, "关于")
        about_card.pack(fill="x", padx=4, pady=(0, 10))
        self._row(
            about_card.body,
            "启动器版本",
            "",
            lambda row: ctk.CTkLabel(
                row,
                text=f"Ge 启动器 {__version__}",
                anchor="e",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
            ),
        )
        self._row(
            about_card.body,
            "GitHub 仓库",
            "https://github.com/StudentBuilding-Studio/GeLauncher",
            lambda row: ctk.CTkButton(
                row,
                text="打开 GitHub 仓库",
                width=170,
                height=32,
                corner_radius=8,
                fg_color=theme.ACCENT,
                hover_color=theme.ACCENT_HOVER,
                text_color="#ffffff",
                command=self._open_repo,
            ),
        )

        self._show_section("general")

        # 即时保存
        self._loading = False
        self._save_job = None
        for variable in (
            self.memory_var,
            self.java_var,
            self.default_width_var,
            self.default_height_var,
            self.default_jvm_var,
            self.default_priority_var,
        ):
            variable.trace_add("write", lambda *_: self._schedule_save())
        self.log_window_var.trace_add("write", lambda *_: self._schedule_save())
        self.source_var.trace_add("write", lambda *_: self._schedule_save())

    def _show_section(self, name: str) -> None:
        if name not in self._setting_sections:
            return
        for key, section in self._setting_sections.items():
            if key == name:
                section.grid()
            else:
                section.grid_remove()
        for key, nav_button in self._setting_nav_buttons.items():
            active = key == name
            nav_button.configure(
                fg_color=theme.ACCENT if active else "transparent",
                text_color="#ffffff" if active else theme.TEXT,
            )

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, app)
        self._build_shell()

    def on_show(self) -> None:
        config = self.app.config
        self._loading = True
        self.memory_var.set(str(config.max_memory_mb))
        self.java_var.set(config.java_path)
        self.log_window_var.set(bool(config.show_log_window))
        source = next(
            (text for text, mode in self.source_labels.items() if mode == config.download_source),
            next(iter(self.source_labels)),
        )
        self.source_var.set(source)
        appearance = next(
            (
                text
                for text, mode in self.appearance_labels.items()
                if mode == config.appearance
            ),
            next(iter(self.appearance_labels)),
        )
        self.appearance_var.set(appearance)
        self.default_width_var.set("" if config.width in (None, "") else str(config.width))
        self.default_height_var.set("" if config.height in (None, "") else str(config.height))
        self.default_jvm_var.set(config.jvm_args or "")
        priority_label = next(
            (
                text
                for text, mode in self.priority_labels.items()
                if mode == (config.process_priority or "normal")
            ),
            "正常",
        )
        self.default_priority_var.set(priority_label)
        self._loading = False
        self.refresh_java_choices()
        env = current_env()
        self.env_var.set(f"系统：{env.os_name} / {env.arch}   游戏目录：{self.app.layout.root}")

    def _step_memory(self, delta: int) -> None:
        try:
            current = int(self.memory_var.get().strip() or self.app.config.max_memory_mb)
        except ValueError:
            current = self.app.config.max_memory_mb
        value = max(512, min(32768, current + delta))
        self.memory_var.set(str(value))

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
                "\n".join(f"Java {major}（{path}）" for major, path in runtimes)
            )

    def _open_repo(self) -> None:
        import webbrowser

        url = "https://github.com/StudentBuilding-Studio/GeLauncher"
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开浏览器", f"{url}\n{exc}")

    def _apply_appearance(self, _value=None) -> None:
        from gelauncher.ui import theme

        mode = self.appearance_labels.get(self.appearance_var.get(), "system")
        theme.set_mode(mode)
        self.app.config.appearance = mode
        self._schedule_save()

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
        config.appearance = self.appearance_labels.get(
            self.appearance_var.get(), config.appearance
        )
        try:
            width = int(self.default_width_var.get().strip())
            config.width = width if width > 0 else None
        except ValueError:
            config.width = None
        try:
            height = int(self.default_height_var.get().strip())
            config.height = height if height > 0 else None
        except ValueError:
            config.height = None
        config.jvm_args = self.default_jvm_var.get().strip()
        config.process_priority = self.priority_labels.get(
            self.default_priority_var.get(), "normal"
        )
        config.save()
