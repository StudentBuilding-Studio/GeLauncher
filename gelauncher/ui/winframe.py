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


def apply_hicon(root, ico_path) -> None:
    """用 LoadImage 按系统图标尺寸加载 ico，再 WM_SETICON。

    Tk 的 iconbitmap 只会挑一帧很小的图标，任务栏（32/48px）看起来会糊；
    这里按 SM_CXICON（DPI 感知）取当前系统真实尺寸。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        hwnd = int(root.wm_frame(), 16)
        if not hwnd:
            return

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x10
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        SM_CXICON, SM_CYICON = 11, 12
        SM_CXSMICON, SM_CYSMICON = 49, 50

        def load(cx: int, cy: int):
            return user32.LoadImageW(
                None, str(ico_path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE
            )

        big = load(
            user32.GetSystemMetrics(SM_CXICON), user32.GetSystemMetrics(SM_CYICON)
        )
        small = load(
            user32.GetSystemMetrics(SM_CXSMICON), user32.GetSystemMetrics(SM_CYSMICON)
        )
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, ctypes.c_void_p(big))
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, ctypes.c_void_p(small))
    except Exception:  # noqa: BLE001 - 设置失败就继续用 Tk 的图标
        pass
