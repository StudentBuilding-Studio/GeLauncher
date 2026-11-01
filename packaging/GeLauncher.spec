# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：pyinstaller --noconfirm --clean packaging/GeLauncher.spec"""

from PyInstaller.utils.hooks import collect_data_files

# CustomTkinter 的主题 JSON、字体、图标都在包数据里，必须一起打进去
datas = collect_data_files("customtkinter")

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=[],
    datas=datas,
    hiddenimports=["darkdetect"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GeLauncher",
)
