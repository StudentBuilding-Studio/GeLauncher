"""单实例锁：避免同时开两个启动器互相覆盖 config / 同时装同一个实例。"""

from __future__ import annotations

import sys

_HANDLE = None


def acquire(name: str = "GeLauncher-SingleInstance") -> bool:
    """拿到命名 mutex 返回 True；已有实例在跑返回 False。"""
    global _HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True  # 拿不到锁就不阻止启动
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _HANDLE = handle
        return True
    except Exception:  # noqa: BLE001
        return True


def notify_already_running(title: str = "Ge 启动器") -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "Ge 启动器已经在运行了。\n请切换到已有窗口，或先关闭它。",
            title,
            0x00000040,  # MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001
        pass
