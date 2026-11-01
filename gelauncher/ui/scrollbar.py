"""轻量滚动条：用一块 Canvas 自己画，避免 CTkScrollbar 每次 set() 都做一轮
圆角矩形重绘 + update_idletasks（拖动窗口时这一项能占几百毫秒）。"""

from __future__ import annotations

import tkinter as tk

from . import theme


class Scrollbar(tk.Canvas):
    def __init__(self, master: tk.Misc, *, command, orientation: str = "vertical") -> None:
        self._vertical = orientation == "vertical"
        super().__init__(
            master,
            width=12 if self._vertical else 12,
            height=12 if self._vertical else 12,
            bg=theme.bg(),
            highlightthickness=0,
            bd=0,
            cursor="arrow",
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hover = False
        self._draw_job = None
        self._drag_offset = 0.0
        self.bind("<Configure>", lambda _e: self._schedule())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        theme.on_change(self._on_theme)

    # ------------------------------------------------------------------ 接口

    def set(self, first, last) -> None:  # noqa: A003 - 和 Tk 滚动条接口一致
        try:
            self._first = float(first)
            self._last = float(last)
        except (TypeError, ValueError):
            return
        self._schedule()

    def _schedule(self) -> None:
        if self._draw_job is not None:
            return
        self._draw_job = self.after(30, self._draw)

    def _on_theme(self) -> None:
        try:
            self.configure(bg=theme.bg())
        except tk.TclError:
            return
        self._schedule()

    def destroy(self) -> None:
        theme.off_change(self._on_theme)
        super().destroy()

    # ------------------------------------------------------------------ 绘制

    def _draw(self) -> None:
        self._draw_job = None
        try:
            self.delete("all")
            length = self.winfo_height() if self._vertical else self.winfo_width()
            if length <= 1:
                return
            if self._last - self._first >= 1.0:
                return  # 内容不足一屏：不画滑块
            start = int(self._first * length)
            end = max(int(self._last * length), start + 24)
            end = min(end, length)
            color = "#9aa4b2" if self._hover else theme.muted()
            if self._vertical:
                self.create_rectangle(2, start, 10, end, fill=color, outline=color)
            else:
                self.create_rectangle(start, 2, end, 10, fill=color, outline=color)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 拖动

    def _set_hover(self, value: bool) -> None:
        if self._hover != value:
            self._hover = value
            self._schedule()

    def _on_press(self, event) -> None:
        if self._last - self._first >= 1.0:
            return
        length = self.winfo_height() if self._vertical else self.winfo_width()
        if length <= 1:
            return
        pos = event.y if self._vertical else event.x
        thumb_start = self._first * length
        thumb_end = self._last * length
        if thumb_start <= pos <= thumb_end:
            self._drag_offset = pos - thumb_start
            return
        # 点击空白：直接跳过去
        self._drag_offset = (thumb_end - thumb_start) / 2
        self._move_to(pos - self._drag_offset, length)

    def _on_drag(self, event) -> None:
        if self._last - self._first >= 1.0:
            return
        length = self.winfo_height() if self._vertical else self.winfo_width()
        pos = event.y if self._vertical else event.x
        self._move_to(pos - self._drag_offset, length)

    def _move_to(self, thumb_start: float, length: int) -> None:
        span = max(self._last - self._first, 1e-6)
        first = max(0.0, min(1.0 - span, thumb_start / length))
        try:
            self._command("moveto", f"{first:.6f}")
        except Exception:  # noqa: BLE001 - 目标控件已销毁
            pass
