@echo off
rem 双击打开 Ge 启动器界面（不弹控制台窗口）
cd /d "%~dp0"
start "" pythonw -m gelauncher ui %*
