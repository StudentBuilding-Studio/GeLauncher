@echo off
rem Open the Ge Launcher window without a console.
cd /d "%~dp0"
start "" pythonw -m gelauncher ui %*
