"""离线账号创建窗口：带命名规则提示和校验。

Minecraft 的玩家名只认大小写英文字母、数字和下划线，中文/空格/符号
进了游戏会出各种怪问题，所以这里直接挡住。
"""

from __future__ import annotations

import re
import tkinter as tk

import customtkinter as ctk

from gelauncher.ui.widgets import button, close_later, label

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_HINT = "只能用大小写英文字母、数字和下划线（_），不要用中文、空格或特殊符号。"


class OfflineAccountDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.title("离线账号")
        self.transient(parent)
        self.resizable(False, False)
        self.geometry("560x300")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=20)
        body.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            body,
            text="玩家名",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        ).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        self.entry = ctk.CTkEntry(body, textvariable=self.name_var, width=320)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        label(body, _HINT, muted=True, wraplength=470).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.error_var = tk.StringVar(value="")
        ctk.CTkLabel(
            body,
            textvariable=self.error_var,
            anchor="w",
            text_color="#dc2626",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(0, 20))
        button(buttons, "取消", self.destroy, width=100).pack(side="right")
        button(buttons, "创建", self._confirm, width=100, accent=True).pack(
            side="right", padx=(0, 8)
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Return>", lambda _e: self._confirm())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(100, self._focus)

    def _focus(self) -> None:
        try:
            self.grab_set()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self.error_var.set("名字不能为空")
            return
        if len(name) > 16:
            self.error_var.set("名字太长，最多 16 个字符")
            return
        if not _NAME_RE.match(name):
            self.error_var.set("含有不允许的字符：" + _HINT)
            return
        self.result = name
        close_later(self)


def ask_offline_name(parent: tk.Misc) -> str | None:
    dialog = OfflineAccountDialog(parent)
    try:
        parent.wait_window(dialog)
    except tk.TclError:
        return None
    return dialog.result
