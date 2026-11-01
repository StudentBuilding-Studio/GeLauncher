"""协作式取消：安装/导入这类长任务用得上。

用法：

    token = CancelToken()
    ... token.cancel()          # UI 线程调用
    if token.cancelled(): ...   # 工作线程自己检查
    token.raise_if_cancelled()  # 或者直接抛异常退出
"""

from __future__ import annotations

import threading


class Cancelled(RuntimeError):
    """任务被用户取消。"""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled("任务已取消")
