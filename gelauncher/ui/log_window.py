"""独立的日志窗口：一个游戏进程配一个（参考 HMCL）。

主窗口不再常驻日志区；日志窗口由"版本设置 → 启用日志窗口"控制是否自动打开，
也可以在版本详情页里手动打开。窗口关掉不影响游戏继续跑。
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from gelauncher.ui.widgets import button

LEVEL_TAGS = {
    "ERROR": "#ff6b6b",
    "WARN": "#ffd166",
    "INFO": "#d4d4d4",
    "LAUNCHER": "#6fb3ff",
}


class LogWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master: tk.Misc,
        version_id: str,
        *,
        log_path: Path | None = None,
        task: bool = False,
        on_cancel=None,
    ) -> None:
        super().__init__(master)
        self.version_id = version_id
        self.log_path = log_path
        self._handle = None
        self.task = task
        self.on_cancel = on_cancel
        self.cancel_button = None
        self.title(f"{'安装/导入' if task else '日志'} - {version_id}")
        self.geometry("900x480")
        self.minsize(560, 280)

        self.autoscroll = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="安装中…" if task else "等待游戏输出…")

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        ctk.CTkCheckBox(
            toolbar,
            text="自动滚动",
            variable=self.autoscroll,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(side="left")
        button(toolbar, "清空", self.clear, width=70, height=30).pack(side="left", padx=(10, 0))
        button(toolbar, "保存到…", self.save_as, width=90, height=30).pack(
            side="left", padx=(6, 0)
        )
        ctk.CTkLabel(
            toolbar,
            textvariable=self.status_var,
            anchor="e",
            text_color="#6b7280",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        ).pack(side="right", fill="x", expand=True)
        if self.task and self.on_cancel is not None:
            self.cancel_button = button(
                toolbar, "取消", self._do_cancel, width=80, height=30, accent=True
            )
            self.cancel_button.pack(side="right", padx=(8, 0))

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            frame,
            wrap="none",
            state="disabled",
            font="TkFixedFont",
            background="#1e1e1e",
            foreground="#d4d4d4",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        for tag, color in LEVEL_TAGS.items():
            self.text.tag_configure(tag, foreground=color)

        from gelauncher.ui.scrollbar import Scrollbar

        yscroll = Scrollbar(frame, orientation="vertical", command=self.text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = Scrollbar(frame, orientation="horizontal", command=self.text.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    def _do_cancel(self) -> None:
        if self.cancel_button is not None:
            self.cancel_button.configure(state="disabled", text="正在取消…")
        self.status_var.set("正在取消，等当前步骤结束…")
        if self.on_cancel is not None:
            self.on_cancel()

    # ------------------------------------------------------------------

    def start_session(self, label: str) -> None:
        """每次新操作打一条醒目的分隔线。"""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.append("")
        self.append("=" * 24 + f" {stamp}  {label} " + "=" * 24)

    def append(self, line: str) -> None:
        tag = "INFO"
        for name in ("ERROR", "WARN", "LAUNCHER"):
            if f"[{name}" in line or f"/{name}]" in line:
                tag = name
                break
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        if self.autoscroll.get():
            self.text.see("end")
        self.text.configure(state="disabled")
        self._write_file(line)

    def _write_file(self, line: str) -> None:
        """日志窗口的内容也落盘，出问题时能翻记录。"""
        if self.log_path is None:
            return
        try:
            if self._handle is None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.log_path.open("a", encoding="utf-8")
            self._handle.write(line + "\n")
            self._handle.flush()
        except OSError:
            self._handle = None

    def destroy(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        super().destroy()

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def save_as(self) -> None:
        content = self.text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("没有内容", "日志还是空的", parent=self)
            return
        default = f"{self.version_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".log",
            initialfile=default,
            filetypes=[("日志", "*.log")],
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self.set_status(f"已保存到 {path}")
