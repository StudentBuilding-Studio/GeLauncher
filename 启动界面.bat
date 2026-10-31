@echo off
rem 双击打开 Ge 启动器界面（保留控制台，方便看报错）
cd /d "%~dp0"
python -m gelauncher ui %*
if errorlevel 1 (
  echo.
  echo 启动失败，错误信息见上方。按任意键关闭。
  pause >nul
)
