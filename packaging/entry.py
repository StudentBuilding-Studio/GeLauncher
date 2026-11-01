"""PyInstaller 入口：GUI 和 CLI 共用同一个 exe。

双击 exe 时没有参数，默认应该打开图形界面；带参数时按 CLI 处理。
"""

import sys

from gelauncher.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("ui")
    sys.exit(main())
