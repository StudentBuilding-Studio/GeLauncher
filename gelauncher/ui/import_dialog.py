"""导入整合包的确认窗口：展示识别结果 + 实例名 / 游戏版本。"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk


def _default_instance(name: str, fallback: str) -> str:
    text = (name or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*]', " ", text).strip().strip(".")
    return text[:48] or fallback


class ModpackImportDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, app, archive, info, on_import) -> None:
        super().__init__(parent)
        self.app = app
        self.archive = archive
        self.info = info
        self.on_import = on_import

        self.title("导入整合包")
        self.transient(parent)
        self.resizable(False, False)
        self.grab_set()

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=info.name, font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(body, text=f"{info.provider} · {info.version or '未知版本'}", foreground="#666").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 10)
        )

        ttk.Label(body, text="游戏版本").grid(row=2, column=0, sticky="w", pady=3)
        self.version_var = tk.StringVar(value=info.game_version or "")
        ttk.Entry(body, textvariable=self.version_var, width=22).grid(
            row=2, column=1, sticky="w", pady=3
        )

        ttk.Label(body, text="加载器").grid(row=3, column=0, sticky="w", pady=3)
        loader_text = (
            f"{info.loader} {info.loader_version or ''}".strip() if info.loader else "原版（无加载器）"
        )
        self.loader_var = tk.StringVar(value=loader_text)
        ttk.Label(body, textvariable=self.loader_var, foreground="#666").grid(
            row=3, column=1, sticky="w", pady=3
        )

        ttk.Label(body, text="实例名").grid(row=4, column=0, sticky="w", pady=3)
        self.instance_var = tk.StringVar(
            value=_default_instance(info.name, info.game_version or "整合包")
        )
        ttk.Entry(body, textvariable=self.instance_var, width=28).grid(
            row=4, column=1, sticky="ew", pady=3
        )

        if info.notes:
            ttk.Label(
                body,
                text="\n".join(f"· {note}" for note in info.notes),
                foreground="#a05000",
                wraplength=380,
                justify="left",
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="导入", command=self._confirm).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.focus_set()

    def _confirm(self) -> None:
        instance = self.instance_var.get().strip()
        version = self.version_var.get().strip()
        if not instance:
            messagebox.showwarning("实例名", "实例名不能为空", parent=self)
            return
        if not version:
            messagebox.showwarning("游戏版本", "请填游戏版本，例如 1.20.1", parent=self)
            return
        error = self.on_import(self.archive, self.info, instance, version)
        if error:
            messagebox.showerror("导入失败", error, parent=self)
            return
        self.destroy()
