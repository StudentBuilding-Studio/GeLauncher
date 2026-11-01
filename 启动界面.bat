@echo off
rem Open the Ge Launcher window and keep the console visible for error output.
cd /d "%~dp0"
python -m gelauncher ui %*
if errorlevel 1 (
  echo.
  echo Failed to start. See the error above. Press any key to close.
  pause >nul
)
