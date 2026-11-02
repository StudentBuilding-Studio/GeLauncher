"""确认框（危险操作/普通确认）。

系统 messagebox 的按钮没法单独染色，所以自绘：
- 危险操作：确认按钮红色
- 普通确认：确认按钮主色蓝

窗口尺寸按内容自适应，并在父窗口（启动器主窗口）居中显示。
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from gelauncher.ui.widgets import button, close_later


class DangerConfirmDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        message: str,
        confirm_text: str = "删除",
        cancel_text: str = "取消",
        danger: bool = True,
    ) -> None:
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        # 先藏起来，等内容排完再算尺寸和位置，避免出现"先大后跳"
        self.withdraw()

        try:
            parent_width = parent.winfo_width()
        except tk.TclError:
            parent_width = 900
        wrap = max(min(int(parent_width * 0.55), 620), 360)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(20, 12))
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
            wraplength=wrap,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(0, 20))
        button(buttons, cancel_text, self._cancel, width=110).pack(side="right")
        button(
            buttons, confirm_text, self._confirm, width=110, danger=danger, accent=not danger
        ).pack(side="right", padx=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after_idle(lambda: self._fit_and_center(parent))
        self.after(120, self._grab)

    def _fit_and_center(self, parent: tk.Misc) -> None:
        try:
            self.update_idletasks()
            width = max(self.winfo_reqwidth(), 480)
            height = max(self.winfo_reqheight(), 180)
            try:
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                pw, ph = parent.winfo_width(), parent.winfo_height()
            except tk.TclError:
                pw = ph = 0
            if pw <= 1 or ph <= 1:
                pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
                px = py = 0
            x = px + max((pw - width) // 2, 0)
            y = py + max((ph - height) // 2, 0)
            # 绕过 CTk 的 geometry 缩放：这里用的已经是实际像素
            tk.Toplevel.geometry(self, f"{width}x{height}+{x}+{y}")
            self.deiconify()
        except tk.TclError:
            pass

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = True
        close_later(self)

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


def ask_confirm(
    parent: tk.Misc,
    *,
    title: str,
    message: str,
    confirm_text: str = "确定",
) -> bool:
    """普通确认框（确认按钮是主色蓝，不是危险红）。"""
    dialog = DangerConfirmDialog(
        parent,
        title=title,
        message=message,
        confirm_text=confirm_text,
        danger=False,
    )
    try:
        parent.wait_window(dialog)
    except tk.TclError:
        return False
    return bool(dialog.result)
