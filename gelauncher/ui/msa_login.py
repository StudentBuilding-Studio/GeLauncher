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

import customtkinter as ctk

from gelauncher.core import microsoft
from gelauncher.ui import theme
from gelauncher.ui.widgets import button, label


class MicrosoftLoginDialog(ctk.CTkToplevel):
    def __init__(self, master: tk.Misc, client_id: str, authority: str = "consumers") -> None:
        super().__init__(master)
        # 用户没配就用启动器内置的 client id（正常情况下用户完全不需要关心这个）
        self.client_id = microsoft.effective_client_id(client_id)
        self.authority = authority or "consumers"
        self.result: dict | None = None
        self.messages: queue.Queue[tuple] = queue.Queue()
        self._cancelled = False
        self._poll_after: str | None = None

        self.title("登录微软账号")
        self.resizable(False, False)
        self.transient(master)
        self.geometry("460x420")

        self.status_var = tk.StringVar(value="正在向微软申请登录代码…")
        self.code_var = tk.StringVar(value="……")
        self.uri_var = tk.StringVar(value="")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.after(120, self._focus)
        self._poll_after = self.after(100, self._poll)
        self.after(200, self._start)

    def _build(self) -> None:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=18)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text="在浏览器里登录微软账号",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=16, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        label(
            frame,
            "1. 打开下面的网址\n2. 输入代码\n3. 用你的微软账号登录（Xbox 账号就是微软账号）",
            muted=True,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))

        ctk.CTkLabel(
            frame,
            textvariable=self.uri_var,
            anchor="w",
            text_color=theme.ACCENT,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, underline=True),
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ctk.CTkEntry(
            frame,
            textvariable=self.code_var,
            font=ctk.CTkFont(family="Consolas", size=20, weight="bold"),
            height=44,
            justify="center",
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 12))
        button(frame, "打开登录页", self._open_browser).grid(
            row=4, column=0, sticky="ew", padx=(0, 4)
        )
        button(frame, "复制代码", self._copy_code).grid(
            row=4, column=1, sticky="ew", padx=(4, 0)
        )

        label(
            frame,
            muted=True,
            textvariable=self.status_var,
            wraplength=400,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(14, 8))
        button(frame, "取消", self._on_cancel).grid(
            row=6, column=0, columnspan=2, sticky="ew"
        )

    def _focus(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

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
        self._poll_after = None
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
                    self._cancelled = True
                    self.destroy()
                    return
                elif kind == "error":
                    self.status_var.set(str(payload))
        except queue.Empty:
            pass
        if not self._cancelled and self.winfo_exists():
            self._poll_after = self.after(100, self._poll)

    def destroy(self) -> None:
        self._cancelled = True
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        super().destroy()

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
