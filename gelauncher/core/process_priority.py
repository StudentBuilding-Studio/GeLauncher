"""调整游戏进程优先级（Windows）。

启动器只负责设置一次；设置失败不报错（权限/系统策略限制很常见）。
"""

from __future__ import annotations

import sys

#: Windows PriorityClass 常量
PRIORITY_CLASSES = {
    "idle": 0x00000040,      # 低
    "below": 0x00004000,     # 低于正常
    "normal": 0x00000020,    # 正常
    "above": 0x00008000,     # 高于正常
    "high": 0x00000080,      # 高
}

PRIORITY_LABELS = {
    "低": "idle",
    "低于正常": "below",
    "正常": "normal",
    "高于正常": "above",
    "高": "high",
}


def apply_priority(pid: int, mode: str) -> bool:
    """把 pid 的优先级改成 mode；成功返回 True。"""
    if sys.platform != "win32":
        return False
    flag = PRIORITY_CLASSES.get(mode)
    if not flag:
        return False
    try:
        import ctypes

        PROCESS_SET_INFORMATION = 0x0200
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            return bool(kernel32.SetPriorityClass(handle, flag))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 优先级是锦上添花，失败就算了
        return False
