"""导入整合包的确认窗口：展示识别结果 + 实例名 / 游戏版本。"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from gelauncher.ui import theme
from gelauncher.ui.widgets import Dialog, close_later, button, label


def _default_instance(name: str, fallback: str) -> str:
    text = (name or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*]', " ", text).strip().strip(".")
    return text[:48] or fallback


class ModpackImportDialog(Dialog):
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

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=16)
        body.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            body,
            text=info.name,
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=15, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        label(
            body,
            f"{info.provider} · {info.version or '未知版本'}",
            muted=True,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))

        label(body, "游戏版本").grid(row=2, column=0, sticky="w", pady=4)
        self.version_var = tk.StringVar(value=info.game_version or "")
        ctk.CTkEntry(body, textvariable=self.version_var, width=220).grid(
            row=2, column=1, sticky="w", pady=4
        )

        label(body, "加载器").grid(row=3, column=0, sticky="w", pady=4)
        loader_text = (
            f"{info.loader} {info.loader_version or ''}".strip() if info.loader else "原版（无加载器）"
        )
        self.loader_var = tk.StringVar(value=loader_text)
        label(body, muted=True, textvariable=self.loader_var).grid(
            row=3, column=1, sticky="w", pady=4
        )

        label(body, "实例名").grid(row=4, column=0, sticky="w", pady=4)
        self.instance_var = tk.StringVar(
            value=_default_instance(info.name, info.game_version or "整合包")
        )
        ctk.CTkEntry(body, textvariable=self.instance_var, width=300).grid(
            row=4, column=1, sticky="ew", pady=4
        )

        if info.notes:
            label(
                body,
                "\n".join(f"· {note}" for note in info.notes),
                wraplength=520,
                text_color="#a05000",
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
        button(buttons, "取消", self.destroy).pack(side="right")
        button(buttons, "导入", self._confirm, accent=True).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(120, self._focus_self)

    def _focus_self(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

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
        close_later(self)
