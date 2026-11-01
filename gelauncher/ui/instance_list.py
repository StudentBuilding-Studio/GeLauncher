"""实例/目录列表：单 Canvas 自绘。

之前用 CTkScrollableFrame + 每行 4 个 CTkLabel，切页时销毁/重建上百个控件，
每个 CTk 控件都带 Canvas、字体对象和 <Configure> 回调，导致切页卡顿、闪烁，
拖动窗口时 CTk 的缩放追踪器还要逐个遍历，更不跟手。

现在整张列表只有一个 Canvas：行是矩形+文字，重绘只清一次 Canvas，
hover/选中只是改几个 item 的颜色，切换页面基本无感。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import theme



class InstanceList(tk.Frame):
    """行为上像 Treeview，绘制上是一块 Canvas。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        columns: tuple[str, ...] = (),
        headers: tuple[str, ...] = (),
        on_row=None,
        on_double=None,
        column_widths: tuple[int, ...] | None = None,
        row_height: int = 40,
        **kwargs,
    ) -> None:
        kwargs.pop("fg_color", None)
        kwargs.pop("corner_radius", None)
        super().__init__(master, bg=theme.bg(), highlightthickness=0, bd=0, **kwargs)
        self.columns = tuple(columns)
        self.headers = tuple(headers) or ("",)
        self.on_row = on_row
        self.on_double = on_double
        self.column_widths = column_widths or (260, 110, 100, 80)
        self.scale = theme.scale_factor()
        self.row_height = max(int(row_height * self.scale), 28)
        self._items: dict[str, dict] = {}
        self._selected: str | None = None
        self._hover: str | None = None
        self._select_callbacks: list = []
        self._font_name = tkfont.Font(family="Microsoft YaHei UI", size=12)
        self._font_value = tkfont.Font(family="Microsoft YaHei UI", size=11)
        self._font_header = tkfont.Font(family="Microsoft YaHei UI", size=11)
        self._redraw_job = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._header = tk.Canvas(
            self,
            width=10,
            height=int(34 * self.scale),
            bg=theme.bg(),
            highlightthickness=0,
            bd=0,
        )
        self._header.grid(row=0, column=0, columnspan=2, sticky="ew")

        self._body = tk.Canvas(
            self,
            width=10,
            bg=theme.bg(),
            highlightthickness=0,
            bd=0,
            yscrollincrement=1,
        )
        self._body.grid(row=1, column=0, sticky="nsew")

        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._body.yview)
        self._scrollbar.grid(row=1, column=1, sticky="ns")
        self._body.configure(yscrollcommand=self._scrollbar.set)

        self._last_width = 0
        self._body.bind("<Configure>", self._on_body_configure)
        self._body.bind("<MouseWheel>", self._on_wheel)
        self._body.bind("<Button-4>", lambda _e: self._scroll_rows(-3))
        self._body.bind("<Button-5>", lambda _e: self._scroll_rows(3))
        self._body.bind("<Motion>", self._on_motion)
        self._body.bind("<Leave>", lambda _e: self._set_hover(None))
        self._body.bind("<Button-1>", self._on_click)
        self._body.bind("<Double-1>", self._on_double)
        self._header.bind("<Configure>", lambda _e: self.after(30, self._draw_header))
        self._theme_callback = self._on_theme_change
        theme.on_change(self._theme_callback)

    def destroy(self) -> None:
        theme.off_change(getattr(self, "_theme_callback", None))
        super().destroy()

    def _on_theme_change(self) -> None:
        try:
            self.configure(bg=theme.bg())
            self._header.configure(bg=theme.bg())
            self._body.configure(bg=theme.bg())
        except tk.TclError:
            return
        self._schedule_redraw()

    # ------------------------------------------------------------------ Treeview 兼容 API

    def heading(self, *_args, **_kwargs) -> None:
        return None

    def column(self, *_args, **_kwargs) -> None:
        return None

    def configure(self, *_args, **_kwargs):
        try:
            return super().configure(*_args, **_kwargs)
        except tk.TclError:
            return None

    def get_children(self, *_args) -> tuple[str, ...]:
        return tuple(self._items)

    def item(self, iid: str, option: str | None = None):
        data = self._items.get(iid, {"text": "", "values": []})
        if option is None:
            return {"text": data["text"], "values": list(data["values"])}
        return data["text"] if option == "text" else list(data["values"])

    def selection(self) -> tuple[str, ...]:
        return (self._selected,) if self._selected else ()

    def selection_set(self, iid: str) -> None:
        if iid in self._items:
            self._apply_selection(iid)

    def see(self, iid: str) -> None:
        self.selection_set(iid)
        try:
            index = list(self._items).index(iid)
        except ValueError:
            return
        total = max(len(self._items) * self.row_height, 1)
        self._body.yview_moveto(max(0.0, (index * self.row_height) / total))

    def bind(self, sequence: str, func, add=None) -> None:  # noqa: A003
        if sequence == "<<TreeviewSelect>>":
            self._select_callbacks.append(func)
        return None

    def yview(self, *_args) -> None:
        return None

    # ------------------------------------------------------------------ 数据

    def insert(self, _parent, _index, *, iid: str, text: str = "", values=(), **_kw) -> None:
        self._items[iid] = {
            "text": text,
            "values": list(values) if values else [],
        }
        if self._selected is None:
            self._selected = iid
        self._schedule_redraw()

    def delete(self, *iids: str) -> None:
        for iid in iids:
            self._items.pop(iid, None)
            if self._selected == iid:
                self._selected = None
            if self._hover == iid:
                self._hover = None
        self._schedule_redraw()

    # ------------------------------------------------------------------ 绘制

    def _schedule_redraw(self) -> None:
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(20, self._redraw)

    def _on_body_configure(self, event) -> None:
        # 拖动窗口时宽度连续变化，只在跨过阈值时重排，避免每帧重绘
        if abs(event.width - self._last_width) < 8:
            return
        self._last_width = event.width
        self._schedule_redraw()

    def _redraw(self) -> None:
        self._redraw_job = None
        canvas = self._body
        width = max(canvas.winfo_width(), 10)
        canvas.delete("row")
        y = 0
        for index, (iid, data) in enumerate(self._items.items()):
            bg = theme.color(theme.ROW_BG if index % 2 == 0 else theme.ROW_ALT)
            if iid == self._selected:
                bg = theme.color(theme.ROW_SELECTED)
            elif iid == self._hover:
                bg = theme.color(theme.ROW_HOVER)
            canvas.create_rectangle(
                4,
                y + 2,
                width - 4,
                y + self.row_height - 2,
                fill=bg,
                outline=theme.border(),
                tags=("row",),
            )
            self._draw_row_text(data, 14, y, width)
            y += self.row_height
        canvas.configure(scrollregion=(0, 0, width, max(y, self.row_height)))
        self._draw_header(width)

    def _column_layout(self, width: int, slots: int) -> list[tuple[int, int]]:
        """返回每个值列的 (中心 x, 列宽)：按内容需要 + 可用空间自适应。"""
        if slots <= 0:
            return []
        base = list(self.column_widths[1:])[:slots]
        needed = []
        for index in range(slots):
            longest = 0
            for data in self._items.values():
                values = data["values"]
                if index < len(values):
                    longest = max(longest, self._font_value.measure(str(values[index])))
            needed.append(longest + 28)
        widths = [max(b, n) for b, n in zip(base, needed)]
        available = max(width - 150, 120)  # 左边给名字留位置
        total = sum(widths)
        if total > available:
            factor = available / total
            widths = [max(52, int(item * factor)) for item in widths]
        layout: list[tuple[int, int]] = []
        right = width - 10
        for slot_width in reversed(widths):
            right -= slot_width // 2
            layout.append((right, slot_width))
            right -= slot_width // 2
        layout.reverse()
        return layout

    def _fit_text(self, text: str, font, limit: int) -> str:
        original = text
        while text and font.measure(text) > limit:
            text = text[:-1]
        if text != original:
            text = text[:-1] + "…"
        return text

    def _draw_row_text(self, data: dict, x: int, y: int, width: int) -> None:
        canvas = self._body
        middle = y + self.row_height // 2
        values = data["values"]
        slots = max(len(self.column_widths) - 1, 0)
        layout = self._column_layout(width, slots)
        name_limit = (layout[0][0] - layout[0][1] // 2 - 20) if layout else width - 20
        canvas.create_text(
            x,
            middle,
            text=self._fit_text(str(data["text"]), self._font_name, max(name_limit, 40)),
            anchor="w",
            fill=theme.text_color(),
            font=self._font_name,
            tags=("row",),
        )
        for index, value in enumerate(values[:slots]):
            center, slot_width = layout[index]
            canvas.create_text(
                center,
                middle,
                text=self._fit_text(str(value), self._font_value, max(slot_width - 10, 30)),
                anchor="center",
                fill=theme.muted(),
                font=self._font_value,
                tags=("row",),
            )

    def _draw_header(self, width: int | None = None) -> None:
        canvas = self._header
        width = width or max(canvas.winfo_width(), 10)
        canvas.delete("all")
        canvas.create_rectangle(
            0, 0, width, canvas.winfo_height(), fill=theme.bg(), outline=theme.bg()
        )
        middle = canvas.winfo_height() // 2
        canvas.create_text(
            14,
            middle,
            text=self.headers[0] if self.headers else "",
            anchor="w",
            fill=theme.muted(),
            font=self._font_header,
        )
        slots = max(len(self.column_widths) - 1, 0)
        layout = self._column_layout(width, slots)
        for index, title in enumerate(self.headers[1 : slots + 1]):
            center, slot_width = layout[index]
            canvas.create_text(
                center,
                middle,
                text=self._fit_text(title, self._font_header, max(slot_width - 8, 30)),
                anchor="center",
                fill=theme.muted(),
                font=self._font_header,
            )

    # ------------------------------------------------------------------ 交互

    def _row_at(self, event_y: int) -> str | None:
        if self.row_height <= 0:
            return None
        y = self._body.canvasy(event_y)
        index = int(y // self.row_height)
        items = list(self._items)
        if 0 <= index < len(items):
            return items[index]
        return None

    def _on_click(self, event) -> None:
        iid = self._row_at(event.y)
        if iid is None:
            return
        self.selection_set(iid)
        if self.on_row is not None:
            self.on_row(iid)

    def _on_double(self, event) -> None:
        iid = self._row_at(event.y)
        if iid is not None and self.on_double is not None:
            self.on_double(iid)

    def _on_motion(self, event) -> None:
        self._set_hover(self._row_at(event.y))

    def _set_hover(self, iid: str | None) -> None:
        if iid == self._hover:
            return
        self._hover = iid
        self._schedule_redraw()

    def _scroll_rows(self, rows: int) -> None:
        self._body.yview_scroll(rows * self.row_height, "units")

    def _on_wheel(self, event) -> None:
        """滚轮：一格滚 3 行（yscrollincrement=1，直接传 3 只有 3 像素，慢得离谱）。"""
        if not event.delta:
            return
        notches = -1 if event.delta > 0 else 1
        self._scroll_rows(notches * 3)

    def _apply_selection(self, iid: str) -> None:
        self._selected = iid
        self._schedule_redraw()
        for callback in list(self._select_callbacks):
            try:
                callback(None)
            except Exception:  # noqa: BLE001 - 回调出错不影响选择
                pass
