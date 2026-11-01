"""选加载器版本安装的对话框（Fabric / Forge / NeoForge 共用）。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk


class LoaderInstallDialog(tk.Toplevel):
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
        self.geometry("460x420")

        self.versions: list[str] = []
        self.ready = False
        self._pending: tuple[str, object] | None = None
        self._poll_after: str | None = None

        header = ttk.Frame(self, padding=(10, 10, 10, 0))
        header.pack(fill="x")
        ttk.Label(
            header,
            text=f"{self.instance}（MC {self.actual}）可用的 {self.label} 版本",
        ).pack(side="left")

        toolbar = ttk.Frame(self, padding=(10, 8, 10, 0))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="过滤").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.filter_var, width=18)
        entry.pack(side="left", padx=(4, 0))
        entry.bind("<KeyRelease>", lambda _e: self.refresh_rows())
        ttk.Button(toolbar, text="刷新", command=self.fetch).pack(side="right")

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, show="tree", selectmode="browse")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _e: self.install_selected())

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value=f"正在拉取 {self.label} 版本列表…")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#666").pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        self.install_button = ttk.Button(bottom, text="安装", command=self.install_selected)
        self.install_button.pack(side="right", padx=(0, 8))
        self.install_button.configure(state="disabled")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.fetch()
        self.after(50, self._focus_self)

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
        # 最新的排前面
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
        # 参数顺序必须和 app.install_loader(instance, kind, loader_version) 对齐
        self.on_choose(self.instance, self.kind, version)
