"""CustomTkinter 版的"实例列表"。

ttk.Treeview 在高 DPI 下要么行高被写死、要么选中样式很土，而且各行的
上下留白没法统一。这里用 CTkScrolledFrame 自己画行：固定行高、固定间距、
hover/选中高亮，顺便保留了一小部分 Treeview 的 API（get_children/insert/
delete/selection…），页面代码基本不用改。
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

LIGHT_TEXT = ("#1a1a1a", "#f2f2f2")
LIGHT_MUTED = ("#6b7280", "#9aa4b2")
ROW_BG = ("#ffffff", "#2b2b2b")
ROW_HOVER = ("#f1f5f9", "#333a44")
ROW_SELECTED = ("#dbeafe", "#1e3a5f")
HEADER_TEXT = ("#6b7280", "#9aa4b2")


class InstanceList(ctk.CTkScrollableFrame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        columns: tuple[str, ...] = (),
        headers: tuple[str, ...] = (),
        on_row=None,
        column_widths: tuple[int, ...] | None = None,
        row_height: int = 40,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", "transparent")
        kwargs.setdefault("corner_radius", 0)
        super().__init__(master, **kwargs)
        self.columns = tuple(columns)
        self.headers = tuple(headers) or ("",)
        self.on_row = on_row
        self.column_widths = column_widths or (260, 110, 100, 80)
        self.row_height = row_height
        self._items: dict[str, dict] = {}
        self._rows: dict[str, ctk.CTkFrame] = {}
        self._selected: str | None = None
        self._select_callbacks: list = []
        self.columnconfigure(0, weight=1)

        if self.headers and any(self.headers):
            header = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
            header.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 2))
            self._setup_columns(header)
            for index, title in enumerate(self.headers):
                ctk.CTkLabel(
                    header,
                    text=title,
                    anchor="w",
                    text_color=HEADER_TEXT,
                    font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                ).grid(row=0, column=index, sticky="w", padx=(8, 4))

    # ------------------------------------------------------------------ Treeview 兼容 API

    def heading(self, *_args, **_kwargs) -> None:
        return None

    def column(self, *_args, **_kwargs) -> None:
        return None

    def configure(self, *_args, **_kwargs) -> None:
        return None

    def get_children(self, *_args) -> tuple[str, ...]:
        return tuple(self._items)

    def item(self, iid: str, option: str | None = None):
        data = self._items.get(iid, {"text": "", "values": []})
        if option is None:
            return dict(data)
        return data.get(option, "" if option != "values" else [])

    def selection(self) -> tuple[str, ...]:
        return (self._selected,) if self._selected else ()

    def selection_set(self, iid: str) -> None:
        if iid in self._items:
            self._apply_selection(iid)

    def see(self, iid: str) -> None:
        self.selection_set(iid)
        try:
            self.update_idletasks()
            row = self._rows[iid]
            total = max(row.winfo_height(), 1) * max(len(self._rows), 1)
            self._parent_canvas.yview_moveto(max(0.0, row.winfo_y() / max(total, 1)))
        except (KeyError, tk.TclError):
            pass

    def bind(self, sequence: str, func, add=None) -> None:  # noqa: A003
        if sequence == "<<TreeviewSelect>>":
            self._select_callbacks.append(func)
        return None

    def yview(self, *_args) -> None:
        return None

    # ------------------------------------------------------------------ 数据

    def insert(self, _parent, _index, *, iid: str, text: str = "", values=(), **_kw) -> None:
        values = list(values) if values else []
        self._items[iid] = {"text": text, "values": values}
        row = ctk.CTkFrame(
            self,
            corner_radius=6,
            fg_color=ROW_BG,
            height=self.row_height,
            border_width=0,
        )
        row.grid(
            row=len(self._items),
            column=0,
            sticky="ew",
            padx=4,
            pady=(0, 3),
        )
        row.columnconfigure(0, weight=1)
        row.grid_propagate(False)
        self._setup_columns(row)

        title = ctk.CTkLabel(
            row,
            text=text,
            anchor="w",
            text_color=LIGHT_TEXT,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )
        title.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=6)
        widgets = [title]
        for index, value in enumerate(values[: len(self.column_widths) - 1], start=1):
            label = ctk.CTkLabel(
                row,
                text=str(value),
                anchor="center",
                text_color=LIGHT_MUTED,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            )
            label.grid(row=0, column=index, sticky="ew", padx=(4, 6), pady=6)
            widgets.append(label)

        for widget in (row, *widgets):
            widget.bind("<Button-1>", lambda _e, key=iid: self._clicked(key))
        row.bind("<Enter>", lambda _e, key=iid: self._hover(key, True))
        row.bind("<Leave>", lambda _e, key=iid: self._hover(key, False))
        self._rows[iid] = row
        if len(self._items) == 1 and self._selected is None:
            self.selection_set(iid)

    def delete(self, *iids: str) -> None:
        for iid in iids:
            row = self._rows.pop(iid, None)
            self._items.pop(iid, None)
            if row is not None:
                try:
                    row.destroy()
                except tk.TclError:
                    pass
            if self._selected == iid:
                self._selected = None
        for index, iid in enumerate(self._items, start=1):
            try:
                self._rows[iid].grid_configure(row=index)
            except (KeyError, tk.TclError):
                pass

    # ------------------------------------------------------------------ 内部

    def _setup_columns(self, frame: ctk.CTkFrame) -> None:
        frame.columnconfigure(0, weight=1)
        for index, width in enumerate(self.column_widths[1:], start=1):
            frame.columnconfigure(index, weight=0, minsize=width)

    def _clicked(self, iid: str) -> None:
        self.selection_set(iid)
        if self.on_row is not None:
            self.on_row(iid)

    def _hover(self, iid: str, entering: bool) -> None:
        if iid == self._selected:
            return
        row = self._rows.get(iid)
        if row is None:
            return
        try:
            row.configure(fg_color=ROW_HOVER if entering else ROW_BG)
        except tk.TclError:
            pass

    def _apply_selection(self, iid: str) -> None:
        previous = self._selected
        self._selected = iid
        for key in (previous, iid):
            row = self._rows.get(key) if key else None
            if row is None:
                continue
            try:
                row.configure(fg_color=ROW_SELECTED if key == iid else ROW_BG)
            except tk.TclError:
                pass
        for callback in list(self._select_callbacks):
            try:
                callback(None)
            except Exception:  # noqa: BLE001 - 回调出错不能影响选择
                pass
