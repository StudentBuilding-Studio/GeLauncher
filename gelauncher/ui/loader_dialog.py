"""选加载器版本安装的对话框（Fabric / Forge / NeoForge 共用）。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from gelauncher.ui.instance_list import InstanceList
from gelauncher.ui.widgets import button, label


class LoaderInstallDialog(ctk.CTkToplevel):
    """拉取某个实例（对应真实 MC 版本）可用的加载器版本，选一个装进去。"""

    def __init__(self, parent: tk.Misc, app, instance: str, actual: str, kind: str, on_choose) -> None:
        super().__init__(parent)
        from gelauncher.core.install_loader import LOADER_LABELS

        self.app = app
        self.instance = instance
        self.actual = actual
        self.kind = kind
        self.on_choose = on_choose
        self.label = LOADER_LABELS.get(kind, kind)
        self.title(f"安装 {self.label}")
        self.transient(parent)
        self.geometry("520x480")
        self.minsize(420, 380)

        self.versions: list[str] = []
        self.ready = False
        self._pending: tuple[str, object] | None = None
        self._poll_after: str | None = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(14, 6))
        label(
            header,
            f"{self.instance}（MC {self.actual}）可用的 {self.label} 版本",
        ).pack(side="left")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(0, 6))
        label(toolbar, "过滤").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ctk.CTkEntry(toolbar, textvariable=self.filter_var, width=180)
        self.filter_entry.pack(side="left", padx=(6, 0))
        self.filter_entry.bind("<KeyRelease>", lambda _e: self.refresh_rows())
        button(toolbar, "刷新", self.fetch).pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = InstanceList(
            body,
            headers=("版本",),
            column_widths=(380,),
            on_double=lambda _iid: self.install_selected(),
        )
        self.tree.grid(row=0, column=0, sticky="nsew")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(8, 14))
        self.status_var = tk.StringVar(value=f"正在拉取 {self.label} 版本列表…")
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
        self.status_var.set(f"正在拉取 {self.label} 版本列表…")
        fetcher = self.app.make_fetcher()
        kind, actual = self.kind, self.actual

        def work() -> None:
            from gelauncher.core import install_loader as core

            try:
                if kind == "fabric":
                    versions = core.fabric_loader_versions(fetcher, actual)
                elif kind == "forge":
                    versions = core.forge_versions_from_mirror(fetcher, actual)
                else:
                    versions = core.neoforge_versions(fetcher, actual)
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
            state, payload = pending
            if state == "ok":
                self._loaded(list(payload))  # type: ignore[arg-type]
            else:
                self.status_var.set(f"拉取失败：{payload}")
        self._poll_after = self.after(80, self._poll)

    def destroy(self) -> None:
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        super().destroy()

    def _loaded(self, versions: list) -> None:
        if not self.winfo_exists():
            return
        from gelauncher.core.install_loader import _version_sort_key

        unique = {str(v) for v in versions if v}
        self.versions = sorted(unique, key=_version_sort_key, reverse=True)
        self.ready = True
        self.status_var.set(f"共 {len(self.versions)} 个版本")
        self.refresh_rows()

    # ------------------------------------------------------------------ 列表

    def refresh_rows(self) -> None:
        if not self.ready:
            return
        keyword = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for version in self.versions:
            if keyword and keyword not in version.lower():
                continue
            self.tree.insert("", "end", iid=version, text=version)
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.see(children[0])
            self.install_button.configure(state="normal")
        else:
            self.install_button.configure(state="disabled")

    def install_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("还没选版本", "先在列表里点一个版本", parent=self)
            return
        version = selection[0]
        self.destroy()
        self.on_choose(self.instance, self.kind, version)
