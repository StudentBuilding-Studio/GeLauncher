"""PyInstaller 入口：GUI 和 CLI 共用同一个 exe。"""

import sys

from gelauncher.cli import main

if __name__ == "__main__":
    sys.exit(main())
