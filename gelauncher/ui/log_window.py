"""独立的日志窗口：一个游戏进程配一个（参考 HMCL）。

主窗口不再常驻日志区；日志窗口由"版本设置 → 启用日志窗口"控制是否自动打开，
也可以在版本详情页里手动打开。窗口关掉不影响游戏继续跑。
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

LEVEL_TAGS = {
    "ERROR": "#ff6b6b",
    "WARN": "#ffd166",
    "INFO": "#d4d4d4",
    "LAUNCHER": "#6fb3ff",
}


class LogWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, version_id: str, *, log_path: Path | None = None) -> None:
        super().__init__(master)
        self.version_id = version_id
        self.log_path = log_path
        self._handle = None
        self.title(f"日志 - {version_id}")
        self.geometry("900x480")
        self.minsize(560, 280)

        self.autoscroll = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="等待游戏输出…")

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(6, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(toolbar, text="自动滚动", variable=self.autoscroll).pack(side="left")
        ttk.Button(toolbar, text="清空", width=8, command=self.clear).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="保存到…", width=10, command=self.save_as).pack(side="left", padx=6)
        ttk.Label(toolbar, textvariable=self.status_var, anchor="e").pack(
            side="right", fill="x", expand=True
        )

        frame = ttk.Frame(self, padding=(6, 0, 6, 6))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            frame,
            wrap="none",
            state="disabled",
            font="TkFixedFont",
            background="#1e1e1e",
            foreground="#d4d4d4",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        for tag, color in LEVEL_TAGS.items():
            self.text.tag_configure(tag, foreground=color)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    # ------------------------------------------------------------------

    def start_session(self, label: str) -> None:
        """每次新操作打一条醒目的分隔线。

        之前踩过坑：日志窗口按版本复用，旧内容里留着一句"装好了"，
        新的安装卡在前面时，用户会以为这次也成功了。
        """
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
        """日志窗口的内容也落盘，出问题时能翻记录（安装日志以前只存在内存里）。"""
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
            parent=self, defaultextension=".log", initialfile=default, filetypes=[("日志", "*.log")]
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self.set_status(f"已保存到 {path}")
