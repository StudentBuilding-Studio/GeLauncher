"""Windows 高 DPI 支持。

Tk 默认是"DPI 不感知"进程：系统按 96 DPI 渲染整窗再拉伸到 150%/200%，
结果就是标题栏清晰、内容全糊。这里在创建窗口前声明 per-monitor DPI 感知，
再按实际 DPI 设置 Tk 的缩放（tk scaling），让字体和控件按真实像素渲染。
"""

from __future__ import annotations

import sys


def enable_dpi_awareness() -> None:
    """必须在创建 Tk 之前调用。失败就静默放过（非 Windows / 老系统）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE = 2（Win8.1+）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def system_dpi() -> int:
    """当前主屏 DPI（96 是 100%）。拿不到就返回 96。"""
    if sys.platform != "win32":
        return 96
    try:
        import ctypes

        # Win10 1607+：GetDpiForSystem
        try:
            value = ctypes.windll.user32.GetDpiForSystem()
            if value:
                return int(value)
        except Exception:  # noqa: BLE001
            pass
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            return int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:  # noqa: BLE001
        return 96


def apply_tk_scaling(root, dpi: int | None = None) -> float:
    """设置 Tk 缩放，返回缩放系数（1.0 = 100%）。"""
    dpi = dpi or system_dpi()
    scale = dpi / 96.0
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:  # noqa: BLE001
        pass
    return scale
