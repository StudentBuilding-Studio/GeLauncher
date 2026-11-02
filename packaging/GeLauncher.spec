# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：pyinstaller --noconfirm --clean packaging/GeLauncher.spec"""

import os

from PyInstaller.utils.hooks import collect_data_files

ICON = os.path.join(SPECPATH, "GeLauncher.ico")
MANIFEST = os.path.join(SPECPATH, "GeLauncher.manifest")
VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")

# CustomTkinter 的主题 JSON、字体、图标都在包数据里，必须一起打进去
datas = collect_data_files("customtkinter")

if os.path.isfile(ICON):
    datas.append((ICON, "."))

# 窗口图标用多尺寸 PNG（每个 DPI 都能拿到原尺寸，避免系统平滑缩放）
import glob as _glob

for _png in _glob.glob(os.path.join(SPECPATH, "GeLauncher-*.png")):
    datas.append((_png, "."))

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=[],
    datas=datas,
    hiddenimports=["darkdetect"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "PIL", "Pillow"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
    manifest=MANIFEST,
    version=VERSION_FILE,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GeLauncher",
)
