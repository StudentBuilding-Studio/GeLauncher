"""选版本安装的对话框：从官方版本列表里挑，而不是手打版本号。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from gelauncher.ui import theme
from gelauncher.ui.instance_list import InstanceList
from gelauncher.ui.widgets import Combo, button, label


class VanillaInstallDialog(ctk.CTkToplevel):
    """列出官方所有版本（正式版 / 快照），选中一个交给启动器安装。"""

    MODES = (("正式版", "release"), ("快照", "snapshot"), ("全部", None))

    def __init__(self, parent: tk.Misc, app, on_choose) -> None:
        super().__init__(parent)
        self.app = app
        self.on_choose = on_choose
        self.title("安装原版")
        self.transient(parent)
        self.geometry("620x560")
        self.minsize(520, 420)

        self.all_versions: list = []
        self.ready = False
        self._poll_after: str | None = None
        self._pending: tuple[str, object] | None = None

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(14, 6))
        label(toolbar, "类型").pack(side="left")
        self.mode_var = tk.StringVar(value=self.MODES[0][0])
        self.mode_combo = Combo(
            toolbar,
            variable=self.mode_var,
            values=[text for text, _ in self.MODES],
            width=110,
        )
        self.mode_combo.pack(side="left", padx=(6, 14))
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_rows())

        label(toolbar, "过滤").pack(side="left")
        self.filter_var = tk.StringVar()
        ctk.CTkEntry(toolbar, textvariable=self.filter_var, width=160).pack(
            side="left", padx=(6, 0)
        )
        self.filter_entry = toolbar.winfo_children()[-1]
        self.filter_entry.bind("<KeyRelease>", lambda _e: self.refresh_rows())
        button(toolbar, "刷新列表", self.fetch).pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = InstanceList(
            body,
            headers=("版本", "类型", "发布时间"),
            column_widths=(300, 90, 170),
            on_double=lambda _iid: self.install_selected(),
            on_row=lambda _iid: self._sync_instance_name(),
        )
        self.tree.grid(row=0, column=0, sticky="nsew")

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=14, pady=(8, 0))
        label(form, "实例名").pack(side="left")
        self.instance_var = tk.StringVar()
        self.instance_entry = ctk.CTkEntry(form, textvariable=self.instance_var, width=280)
        self.instance_entry.pack(side="left", padx=(6, 0))
        self.instance_edited = False
        self.instance_entry.bind("<KeyRelease>", lambda _e: self._mark_edited())

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(8, 14))
        self.status_var = tk.StringVar(value="正在拉取版本列表…")
        label(bottom, muted=True, textvariable=self.status_var).pack(side="left")
        button(bottom, "取消", self.destroy).pack(side="right")
        self.install_button = button(bottom, "安装", self.install_selected, accent=True)
        self.install_button.pack(side="right", padx=(0, 8))
        self.install_button.configure(state="disabled")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.fetch()
        self.after(120, self._focus_self)

    def _focus_self(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 拉取

    def fetch(self) -> None:
        self.ready = False
        self.install_button.configure(state="disabled")
        self.status_var.set("正在拉取版本列表…")
        fetcher = self.app.make_fetcher()

        def work() -> None:
            from gelauncher.core.install_vanilla import remote_versions

            try:
                versions = remote_versions(fetcher)
            except Exception as exc:  # noqa: BLE001
                self._pending = ("error", str(exc))
                return
            self._pending = ("ok", versions)

        threading.Thread(target=work, daemon=True).start()
        if self._poll_after is None:
            self._poll_after = self.after(80, self._poll)

    def _poll(self) -> None:
        self._poll_after = None
        if not self.winfo_exists():
            return
        pending, self._pending = self._pending, None
        if pending is not None:
            kind, payload = pending
            if kind == "ok":
                self._loaded(payload)  # type: ignore[arg-type]
            else:
                self._failed(str(payload))
        self._poll_after = self.after(80, self._poll)

    def destroy(self) -> None:
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        super().destroy()

    def _failed(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self.status_var.set(f"拉取失败：{message}")

    def _loaded(self, versions: list) -> None:
        if not self.winfo_exists():
            return
        self.all_versions = versions
        self.ready = True
        self.refresh_rows()
        release_count = sum(1 for item in versions if item.type == "release")
        self.status_var.set(f"共 {len(versions)} 个版本（正式版 {release_count} 个）")

    # ------------------------------------------------------------------ 列表

    def _current_kind(self) -> str | None:
        for label_text, kind in self.MODES:
            if label_text == self.mode_var.get():
                return kind
        return None

    def _mark_edited(self) -> None:
        self.instance_edited = bool(self.instance_var.get().strip())

    def _sync_instance_name(self) -> None:
        """没手动改过实例名时，跟着选中的版本号自动填。"""
        if self.instance_edited:
            return
        selection = self.tree.selection()
        if selection:
            self.instance_var.set(selection[0])

    def refresh_rows(self) -> None:
        if not self.ready:
            return
        kind = self._current_kind()
        keyword = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for item in self.all_versions:
            if kind and item.type != kind:
                continue
            if keyword and keyword not in item.id.lower():
                continue
            self.tree.insert(
                "",
                "end",
                iid=item.id,
                text=item.id,
                values=("正式版" if item.type == "release" else item.type, item.release_time or ""),
            )
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.see(children[0])
            self.install_button.configure(state="normal")
            self._sync_instance_name()
        else:
            self.install_button.configure(state="disabled")

    def install_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("还没选版本", "先在列表里点一个版本", parent=self)
            return
        version_id = selection[0]
        instance = self.instance_var.get().strip() or version_id
        error = self.on_choose(version_id, instance)
        if error:
            messagebox.showerror("装不了", error, parent=self)
            return
        self.destroy()
