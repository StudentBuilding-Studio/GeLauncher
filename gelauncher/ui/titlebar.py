"""自绘标题栏：大图标 + 可调字体 + 最小化/关闭。

系统标题栏的图标在 100% DPI 下只有 16px，没法变大；这里去掉系统标题栏
（保留 WS_THICKFRAME，所以边缘缩放仍然可用），自己画一条。

不提供最大化按钮：去掉系统标题栏后最大化会盖住任务栏，体验不好。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from gelauncher.ui import theme

HEIGHT = 46
ICON_SIZE = 32


def _packaging_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2] / "packaging"


def _icon_image(target_px: int) -> tk.PhotoImage | None:
    """按 DPI 挑一张最接近目标像素尺寸的 PNG，避免缩放发糊。"""
    sizes = (16, 24, 32, 48, 64, 128, 256)
    picked = min(sizes, key=lambda size: abs(size - target_px))
    path = _packaging_dir() / f"GeLauncher-{picked}.png"
    if not path.is_file():
        return None
    try:
        return tk.PhotoImage(file=str(path))
    except Exception:  # noqa: BLE001
        return None


class TitleBar(ctk.CTkFrame):
    def __init__(self, master, *, title: str, on_close) -> None:
        self._bg = theme.color(theme.BG)
        super().__init__(
            master,
            height=HEIGHT,
            corner_radius=0,
            fg_color=self._bg,
        )
        self._on_close = on_close
        self._drag_offset: tuple[int, int, int, int] | None = None

        scale = theme.scale_factor()
        icon_px = max(int(ICON_SIZE * scale), 16)
        self._icon = _icon_image(icon_px)
        if self._icon is not None:
            # tk.Label 不参与 CTk 缩放，尺寸就是像素，正好用来放原尺寸图标
            icon = tk.Label(
                self,
                image=self._icon,
                bg=self._bg,
                bd=0,
                highlightthickness=0,
            )
            icon.configure(width=icon_px, height=icon_px)
        else:
            icon = ctk.CTkLabel(self, text="Ge", width=ICON_SIZE)
        icon.grid(
            row=0,
            column=0,
            padx=(int(12 * scale), int(8 * scale)),
            pady=0,
        )
        self._icon_label = icon
        self._icon_px = icon_px

        self._title_var = tk.StringVar(value=title)
        self._title = ctk.CTkLabel(
            self,
            textvariable=self._title_var,
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        )
        self._title.grid(row=0, column=1, sticky="w")
        self.columnconfigure(1, weight=1)

        self._buttons: dict[str, ctk.CTkButton] = {}
        for index, (name, glyph, command) in enumerate(
            (
                ("min", "─", self._minimize),
                ("close", "✕", self._close),
            ),
            start=2,
        ):
            button = ctk.CTkButton(
                self,
                text=glyph,
                width=46,
                height=HEIGHT,
                corner_radius=0,
                fg_color="transparent",
                hover_color=("#e2e8f0", "#2a2f37"),
                text_color=theme.TEXT,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                command=command,
            )
            button.grid(row=0, column=index, sticky="e")
            self._buttons[name] = button
        self._buttons["close"].configure(hover_color="#c42b1c", text_color="#ffffff")

        for widget in (self, icon, self._title):
            widget.bind("<ButtonPress-1>", self._start_drag, add="+")

        theme.on_change(self._on_theme)
        self.bind("<Destroy>", lambda _e: theme.off_change(self._on_theme), add="+")

    # ------------------------------------------------------------------ 外观

    def _on_theme(self) -> None:
        self._bg = theme.color(theme.BG)
        try:
            self.configure(fg_color=self._bg)
            try:
                self._icon_label.configure(bg=self._bg)
            except tk.TclError:
                pass
            for button in self._buttons.values():
                button.configure(text_color=theme.TEXT)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 拖动

    def _start_drag(self, event) -> None:
        root = self.winfo_toplevel()
        self._drag_offset = (event.x_root, event.y_root, root.winfo_x(), root.winfo_y())
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<ButtonRelease-1>", self._end_drag, add="+")

    def _on_drag(self, event) -> None:
        if self._drag_offset is None:
            return
        x0, y0, wx, wy = self._drag_offset
        x = wx + event.x_root - x0
        y = wy + event.y_root - y0
        root = self.winfo_toplevel()
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(root.wm_frame(), 16)
                # 直接移动窗口，不触发 Tk 的重新布局，避免拖动时残影
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010
                )
                return
            except Exception:  # noqa: BLE001
                pass
        root.geometry(f"+{x}+{y}")

    def _end_drag(self, _event) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        # 拖动期间绕过 Tk 移动窗口，松手后同步一次位置，避免后续 geometry 跳回
        try:
            self.winfo_toplevel().update_idletasks()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 按钮

    def _minimize(self) -> None:
        self.winfo_toplevel().iconify()

    def _close(self) -> None:
        self._on_close()
