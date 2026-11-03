"""几个复用的 CTk 小部件。"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme


class Combo(ctk.CTkComboBox):
    """CTkComboBox + Treeview 风格的 cget("values")/configure(values=...)，方便测试和复用。"""

    def __init__(
        self, master, *, values=(), variable=None, width=200, readonly: bool = False, **kwargs
    ) -> None:
        self._values = list(values)
        if readonly:
            kwargs.setdefault("state", "readonly")
        super().__init__(
            master,
            values=self._values,
            variable=variable,
            width=width,
            **kwargs,
        )

    def configure(self, **kwargs):  # noqa: A003
        if "values" in kwargs:
            self._values = list(kwargs.pop("values"))
            kwargs["values"] = self._values
        return super().configure(**kwargs)

    config = configure

    def cget(self, key):
        if key == "values":
            return tuple(self._values)
        return super().cget(key)

    def bind(self, sequence, func, add=None):  # noqa: A003
        # CTkComboBox.bind 只接受 add="+" / True，默认值 None 会被它直接拒绝
        return super().bind(sequence, func, add="+")


class Card(ctk.CTkFrame):
    """带标题的卡片容器；真正的控件放进 ``card.body``。"""

    def __init__(self, master, title: str, **kwargs) -> None:
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("fg_color", theme.CARD)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER)
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self,
            text=title,
            anchor="w",
            text_color=theme.MUTED,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.body.columnconfigure(0, weight=1)


def label(master, text: str = "", *, muted: bool = False, **kwargs) -> ctk.CTkLabel:
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    if "text" not in kwargs and "textvariable" not in kwargs:
        kwargs["text"] = text
    kwargs.setdefault("text_color", theme.MUTED if muted else theme.TEXT)
    kwargs.setdefault("font", ctk.CTkFont(family="Microsoft YaHei UI", size=12))
    return ctk.CTkLabel(master, **kwargs)


def button(master, text: str, command, *, accent: bool = False, danger: bool = False, **kwargs) -> ctk.CTkButton:
    kwargs.setdefault("corner_radius", 8)
    kwargs.setdefault("height", 34)
    kwargs.setdefault("font", ctk.CTkFont(family="Microsoft YaHei UI", size=12))
    if danger:
        kwargs.setdefault("fg_color", "#dc2626")
        kwargs.setdefault("hover_color", "#b91c1c")
        kwargs.setdefault("text_color", "#ffffff")
    elif accent:
        kwargs.setdefault("fg_color", theme.ACCENT)
        kwargs.setdefault("hover_color", theme.ACCENT_HOVER)
        kwargs.setdefault("text_color", "#ffffff")
    else:
        kwargs.setdefault("fg_color", ("#f3f4f6", "#2b2f36"))
        kwargs.setdefault("hover_color", ("#e5e7eb", "#363b43"))
        kwargs.setdefault("text_color", theme.TEXT)
    return ctk.CTkButton(master, text=text, command=command, **kwargs)


def close_later(window, delay: int = 120) -> None:
    """先隐藏、稍后再销毁。

    CTkButton 的悬停动画会在 after 里回调自己的 canvas；如果在按钮 command 里
    立刻 destroy 整个 Toplevel，回调就会撞上已销毁的控件，报
    "invalid command name ...!ctkcanvas"。延迟销毁可以避开这个时机。
    """
    try:
        window.withdraw()
    except Exception:  # noqa: BLE001
        pass
    try:
        window.after(delay, window.destroy)
    except Exception:  # noqa: BLE001
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass


def settings_row(parent, title: str, subtitle: str = "", control_factory=None, **kwargs):
    """统一的设置行：左边标题/说明，右边控件。

    实例设置和全局设置都走这个函数，保证两处排版、字号、间距完全一致。
    """
    import tkinter as tk

    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=14, pady=7)
    text = ctk.CTkFrame(row, fg_color="transparent")
    text.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(
        text,
        text=title,
        anchor="w",
        font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
    ).pack(fill="x")
    if subtitle:
        ctk.CTkLabel(
            text,
            text=subtitle,
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=theme.MUTED,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
        ).pack(fill="x", pady=(2, 0))
    if control_factory is not None:
        control = control_factory(row)
        control.pack(side="right", padx=(14, 0))
    return row


class Dialog(ctk.CTkToplevel):
    """所有对话框的基类：启动器图标 + 相对主窗口居中。"""

    def __init__(self, master=None, **kwargs) -> None:
        super().__init__(master, **kwargs)
        try:
            from gelauncher.ui import theme

            theme.apply_window_icon(self)
        except Exception:  # noqa: BLE001 - 图标只是装饰
            pass
        # 等 pack/grid 排完再居中；只改位置，不改尺寸
        self.after(20, self.center_on_parent)

    def center_on_parent(self) -> None:
        try:
            self.update_idletasks()
            width = self.winfo_width() or self.winfo_reqwidth()
            height = self.winfo_height() or self.winfo_reqheight()
            parent = self.master
            root = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
            px, py = root.winfo_rootx(), root.winfo_rooty()
            pw, ph = root.winfo_width(), root.winfo_height()
            if pw <= 1 or ph <= 1:
                px, py = 0, 0
                pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
            x = px + max((pw - width) // 2, 0)
            y = py + max((ph - height) // 2, 0)
            # 物理像素定位，绕过 CTk geometry 的缩放
            import tkinter as tk

            tk.Toplevel.geometry(self, f"+{x}+{y}")
        except Exception:  # noqa: BLE001 - 居中失败不影响功能
            pass
