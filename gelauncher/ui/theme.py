"""外观/主题入口：CustomTkinter 的初始化与配色。

只在这里决定"用什么窗口/什么主题"，其它模块不直接 import customtkinter，
方便以后换别的 UI 工具包。
"""

from __future__ import annotations

import tkinter as tk

try:
    import customtkinter as ctk
except Exception:  # noqa: BLE001 - 没装就退回普通 Tk
    ctk = None

APPEARANCE = "light"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
BG = "#ebebeb"
CARD = "#ffffff"
TEXT = "#1a1a1a"
MUTED = "#6b7280"
BORDER = "#d8dde4"


def available() -> bool:
    return ctk is not None


def make_root() -> tk.Tk:
    if ctk is None:
        return tk.Tk()
    ctk.set_appearance_mode(APPEARANCE)
    ctk.set_default_color_theme("blue")
    return ctk.CTk()


def is_ctk(root) -> bool:
    return ctk is not None and isinstance(root, ctk.CTk)


def style_ttk(root) -> None:
    """把 ttk 控件的颜色调成和 CTk 背景一致（页面里还有不少 ttk 控件）。"""
    from tkinter import ttk

    try:
        import tkinter.font as tkfont

        linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    except Exception:  # noqa: BLE001
        linespace = 20

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=TEXT, fieldbackground="#ffffff")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED)
    style.configure("TButton", padding=(12, 6))
    style.configure("Treeview", background=CARD, fieldbackground=CARD, rowheight=int(linespace * 1.6))
    style.configure("Treeview.Heading", background=BG, foreground=MUTED, relief="flat")
    style.map(
        "Treeview",
        background=[("selected", "#dbeafe")],
        foreground=[("selected", TEXT)],
    )
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(14, 8))
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER)],
        foreground=[("disabled", "#e5e7eb")],
    )
    style.configure(
        "Danger.TButton",
        background="#dc2626",
        foreground="#ffffff",
        padding=(12, 8),
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#b91c1c"), ("pressed", "#991b1b")],
        foreground=[("disabled", "#f3f4f6")],
    )
    style.configure("Nav.TButton", anchor="w", padding=(12, 8))
    style.configure("Status.TLabel", background=BG, foreground=MUTED, padding=(4, 4))


_SCALE_CACHE: float | None = None


def scale_factor() -> float:
    """DPI 缩放系数（1.0 = 100%），缓存一次。"""
    global _SCALE_CACHE
    if _SCALE_CACHE is None:
        try:
            from .dpi import system_dpi

            _SCALE_CACHE = max(1.0, system_dpi() / 96.0)
        except Exception:  # noqa: BLE001
            _SCALE_CACHE = 1.0
    return _SCALE_CACHE
