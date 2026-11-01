"""危险操作确认框（删除版本/卸载加载器之类）。

系统 messagebox 的按钮没法单独染色，所以删除确认改用 CTk 自绘：
确定按钮是红色的，和"删除这个实例…"呼应。
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from gelauncher.ui.widgets import button


class DangerConfirmDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        message: str,
        confirm_text: str = "删除",
        cancel_text: str = "取消",
    ) -> None:
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.geometry("720x340")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)
        body.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            body,
            text="?",
            width=46,
            height=46,
            corner_radius=23,
            fg_color="#2563eb",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="n")
        ctk.CTkLabel(
            body,
            text=message,
            justify="left",
            anchor="w",
            wraplength=600,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(0, 20))
        button(buttons, cancel_text, self._cancel, width=110).pack(side="right")
        button(buttons, confirm_text, self._confirm, width=110, danger=True).pack(
            side="right", padx=(0, 10)
        )

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(80, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()


def ask_danger(
    parent: tk.Misc,
    *,
    title: str,
    message: str,
    confirm_text: str = "删除",
) -> bool:
    dialog = DangerConfirmDialog(
        parent, title=title, message=message, confirm_text=confirm_text
    )
    try:
        parent.wait_window(dialog)
    except tk.TclError:
        return False
    return bool(dialog.result)
