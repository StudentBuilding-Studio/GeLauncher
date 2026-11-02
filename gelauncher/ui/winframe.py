"""去掉系统标题栏，但保留窗口边框（可缩放、任务栏、贴边分屏）。

自定义标题栏由 ui/titlebar.py 负责绘制。
"""

from __future__ import annotations

import sys


def hide_native_titlebar(root) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = 0
        try:
            hwnd = int(root.wm_frame(), 16)
        except Exception:  # noqa: BLE001
            hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        if not hwnd:
            return

        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_CAPTION)

        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except Exception:  # noqa: BLE001 - 非 Windows/权限问题就退回原生标题栏
        pass
