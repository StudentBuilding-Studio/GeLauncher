"""微软账号登录对话框（设备代码流）。

界面流程：
1. 后台拿 device code → 显示 "在浏览器打开 xxx，输入代码 ABCD-EFGH"；
2. 用户在浏览器里登录，界面在后台轮询；
3. 成功后换成 Minecraft 会话，写进配置并返回。

所有网络都在后台线程，主线程只消费队列。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from gelauncher.core import microsoft


class MicrosoftLoginDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, client_id: str, authority: str = "consumers") -> None:
        super().__init__(master)
        self.client_id = client_id
        self.authority = authority or "consumers"
        self.result: dict | None = None
        self.messages: queue.Queue[tuple] = queue.Queue()
        self._cancelled = False

        self.title("登录微软账号")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.status_var = tk.StringVar(value="正在向微软申请登录代码…")
        self.code_var = tk.StringVar(value="……")
        self.uri_var = tk.StringVar(value="")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.after(100, self._poll)
        self.after(200, self._start)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="在浏览器里登录微软账号", font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            frame,
            text="1. 打开下面的网址\n2. 输入代码\n3. 用你的微软账号登录（Xbox 账号就是微软账号）",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 10))

        ttk.Label(frame, textvariable=self.uri_var, foreground="#2d6cdf").grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(frame, textvariable=self.code_var, font=("Consolas", 16, "bold"), width=18, justify="center").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 10)
        )
        ttk.Button(frame, text="打开登录页", command=self._open_browser).grid(
            row=4, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(frame, text="复制代码", command=self._copy_code).grid(
            row=4, column=1, sticky="ew", padx=(4, 0)
        )

        ttk.Label(frame, textvariable=self.status_var, foreground="#666", wraplength=360, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 6)
        )
        ttk.Button(frame, text="取消", command=self._on_cancel).grid(
            row=6, column=0, columnspan=2, sticky="ew"
        )

    # ------------------------------------------------------------------ 后台

    def _start(self) -> None:
        if not self.client_id:
            self.status_var.set(
                "还没配置微软 client id。\n先看 msa-setup.md 注册应用，"
                "再用 `ge config set msa-client-id <id>` 填进去。"
            )
            return

        def work() -> None:
            try:
                device = None
                errors: list[str] = []
                order = [self.authority] + [
                    item for item in microsoft.AUTHORITIES if item != self.authority
                ]
                for authority in order:
                    try:
                        device = microsoft.start_device_flow(self.client_id, authority)
                        self.authority = authority
                        break
                    except microsoft.MSAuthError as exc:
                        errors.append(f"{authority}: {exc}")
                if device is None:
                    raise microsoft.MSAuthError(
                        "两种登录端点都没成功：\n" + "\n".join(errors)
                    )
                self.messages.put(("device", device))
                token = microsoft.poll_device_token(
                    self.client_id,
                    device,
                    on_status=lambda text: self.messages.put(("status", text)),
                )
                self.messages.put(("status", "正在登录 Xbox Live…"))
                session = microsoft.login_with_msa(token)
                self.messages.put(("success", (token, session, self.authority)))
            except microsoft.MinecraftNotFound as exc:
                self.messages.put(("error", str(exc)))
            except microsoft.MSAuthError as exc:
                self.messages.put(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001 - 登录线程不能把界面带崩
                self.messages.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 消息

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "device":
                    self.code_var.set(payload.user_code)
                    self.uri_var.set(payload.verification_uri)
                    self.status_var.set("等你在浏览器里完成登录…")
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "success":
                    token, session, authority = payload
                    self.result = microsoft.account_record(
                        name=session.name,
                        uuid=session.uuid,
                        refresh_token=token.refresh_token,
                        access_token=session.access_token,
                        expires_at=session.expires_at,
                        authority=authority,
                    )
                    self.status_var.set(f"登录成功：{session.name}")
                    self.destroy()
                    return
                elif kind == "error":
                    self.status_var.set(str(payload))
        except queue.Empty:
            pass
        if not self._cancelled:
            self.after(100, self._poll)

    def _open_browser(self) -> None:
        uri = self.uri_var.get()
        if uri:
            webbrowser.open(uri)

    def _copy_code(self) -> None:
        code = self.code_var.get()
        if not code:
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.status_var.set("代码已复制，去浏览器粘贴吧")

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.destroy()


def login_microsoft(master: tk.Misc, client_id: str, authority: str = "consumers") -> dict | None:
    dialog = MicrosoftLoginDialog(master, client_id, authority)
    master.wait_window(dialog)
    return dialog.result
