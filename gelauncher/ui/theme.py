"""外观/主题入口：CustomTkinter 的初始化、配色和深浅色切换。

只在这里决定"用什么窗口/什么主题"，其它模块不直接 import customtkinter。
颜色用 (浅色, 深色) 二元组定义，CTk 控件可直接吃二元组自动切换；
Canvas/ttk 这种只认单一颜色的地方用 color() 解析当前模式。
"""

from __future__ import annotations

import tkinter as tk

try:
    import customtkinter as ctk
except Exception:  # noqa: BLE001 - 没装就退回普通 Tk
    ctk = None

MODE_SYSTEM = "system"
MODE_LIGHT = "light"
MODE_DARK = "dark"

MODE_LABELS = {"跟随系统": MODE_SYSTEM, "浅色": MODE_LIGHT, "深色": MODE_DARK}

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
DANGER = "#dc2626"
DANGER_HOVER = "#b91c1c"

BG = ("#ebebeb", "#111315")
CARD = ("#ffffff", "#1f2124")
TEXT = ("#1a1a1a", "#f2f2f2")
MUTED = ("#6b7280", "#9aa4b2")
BORDER = ("#d8dde4", "#3a3f46")

ROW_BG = ("#ffffff", "#26292e")
ROW_ALT = ("#fbfcfe", "#22252a")
ROW_HOVER = ("#f1f5f9", "#32363d")
ROW_SELECTED = ("#dbeafe", "#1e3a5f")

_current_mode = MODE_SYSTEM
_listeners: list = []
_ttk_root = None
_SCALE_CACHE: float | None = None


def available() -> bool:
    return ctk is not None


def is_ctk(root) -> bool:
    return ctk is not None and isinstance(root, ctk.CTk)


def _icon_path() -> str | None:
    import sys
    from pathlib import Path

    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "GeLauncher.ico")
    repo = Path(__file__).resolve().parents[2]
    candidates.append(repo / "packaging" / "GeLauncher.ico")
    for item in candidates:
        if item.is_file():
            return str(item)
    return None


def apply_window_icon(root) -> None:
    path = _icon_path()
    if path is None:
        return
    try:
        root.iconbitmap(default=path)
    except Exception:  # noqa: BLE001 - 非 Windows/缺 ico 时忽略
        pass


def _fix_default_font() -> None:
    """CustomTkinter 默认字体是 Roboto，Windows 没装会回退成衬线字体。"""
    if ctk is None:
        return
    try:
        ctk.ThemeManager.theme["CTkFont"]["family"] = "Microsoft YaHei UI"
        ctk.ThemeManager.theme["CTkFont"]["size"] = 13
    except Exception:  # noqa: BLE001
        pass


def make_root() -> tk.Tk:
    if ctk is None:
        root = tk.Tk()
        apply_window_icon(root)
        return root
    _fix_default_font()
    ctk.set_appearance_mode(_current_mode)
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    apply_window_icon(root)
    return root


def set_mode(mode: str) -> None:
    """切换主题（system/light/dark），立即应用到所有已注册的界面。"""
    global _current_mode
    _current_mode = mode if mode in (MODE_SYSTEM, MODE_LIGHT, MODE_DARK) else MODE_SYSTEM
    if ctk is not None:
        _fix_default_font()
        ctk.set_appearance_mode(_current_mode)

    def _refresh() -> None:
        if _ttk_root is not None:
            _apply_ttk_style()
            try:
                _ttk_root.update_idletasks()
            except Exception:  # noqa: BLE001
                pass
        for callback in list(_listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - 单个界面出错不影响其它
                pass

    if _ttk_root is not None:
        # CTk 的 get_appearance_mode() 在 set 之后要等它自己的回调跑完才准，
        # 这里推到 idle 再刷，避免 ttk 用旧调色板把页面刷成深色。
        try:
            _ttk_root.after_idle(_refresh)
            _ttk_root.after(60, _refresh)
            return
        except Exception:  # noqa: BLE001
            pass
    _refresh()


def set_initial(mode: str) -> None:
    global _current_mode
    _current_mode = mode if mode in (MODE_SYSTEM, MODE_LIGHT, MODE_DARK) else MODE_SYSTEM
    if ctk is not None:
        _fix_default_font()
        ctk.set_appearance_mode(_current_mode)


def current_mode() -> str:
    return _current_mode


def dark() -> bool:
    if ctk is not None:
        try:
            return ctk.get_appearance_mode().lower() == "dark"
        except Exception:  # noqa: BLE001
            pass
    return _current_mode == MODE_DARK


def color(value) -> str:
    """(浅, 深) 二元组 → 当前模式下的单色。"""
    if isinstance(value, (tuple, list)):
        return value[1] if dark() else value[0]
    return value


def on_change(callback) -> None:
    _listeners.append(callback)


def off_change(callback) -> None:
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def bg() -> str:
    return color(BG)


def card() -> str:
    return color(CARD)


def text_color() -> str:
    return color(TEXT)


def muted() -> str:
    return color(MUTED)


def border() -> str:
    return color(BORDER)


def scale_factor() -> float:
    global _SCALE_CACHE
    if _SCALE_CACHE is None:
        try:
            from .dpi import system_dpi

            _SCALE_CACHE = max(1.0, system_dpi() / 96.0)
        except Exception:  # noqa: BLE001
            _SCALE_CACHE = 1.0
    return _SCALE_CACHE


def style_ttk(root) -> None:
    """把 ttk 控件的颜色调成和当前主题一致（页面里还有不少 ttk 控件）。"""
    global _ttk_root
    _ttk_root = root
    _apply_ttk_style()


def _apply_ttk_style() -> None:
    from tkinter import ttk

    root = _ttk_root
    if root is None:
        return
    try:
        import tkinter.font as tkfont

        linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    except Exception:  # noqa: BLE001
        linespace = 20

    background = bg()
    card_bg = card()
    foreground = text_color()
    muted_color = muted()
    border_color = border()

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=background, foreground=foreground, fieldbackground=card_bg)
    style.configure("TFrame", background=background)
    style.configure("TLabel", background=background, foreground=foreground)
    style.configure("TLabelframe", background=background, bordercolor=border_color)
    style.configure("TLabelframe.Label", background=background, foreground=muted_color)
    style.configure("TButton", padding=(12, 6))
    # clam 的下拉/数值框箭头默认用深色，深色主题下几乎看不见
    style.configure(
        "TCombobox",
        fieldbackground=card_bg,
        background=card_bg,
        foreground=foreground,
        arrowcolor=foreground,
        arrowsize=12,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", card_bg)],
        foreground=[("readonly", foreground)],
        arrowcolor=[("active", ACCENT), ("pressed", ACCENT)],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=card_bg,
        background=card_bg,
        foreground=foreground,
        arrowcolor=foreground,
        arrowsize=12,
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("readonly", card_bg)],
        foreground=[("readonly", foreground)],
        arrowcolor=[("active", ACCENT), ("pressed", ACCENT)],
    )
    style.configure(
        "Treeview", background=card_bg, fieldbackground=card_bg, rowheight=int(linespace * 1.6)
    )
    style.configure(
        "Treeview.Heading", background=background, foreground=muted_color, relief="flat"
    )
    style.map(
        "Treeview",
        background=[("selected", color(ROW_SELECTED))],
        foreground=[("selected", foreground)],
    )
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(14, 8))
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER)],
        foreground=[("disabled", "#e5e7eb")],
    )
    style.configure(
        "Danger.TButton", background=DANGER, foreground="#ffffff", padding=(12, 8)
    )
    style.map(
        "Danger.TButton",
        background=[("active", DANGER_HOVER), ("pressed", "#991b1b")],
        foreground=[("disabled", "#f3f4f6")],
    )
    style.configure("Nav.TButton", anchor="w", padding=(12, 8))
    style.configure(
        "Status.TLabel", background=background, foreground=muted_color, padding=(4, 4)
    )
