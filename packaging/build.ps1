# 打包成 dist/GeLauncher/（目录分发）
# 用法：在仓库根目录执行  powershell -ExecutionPolicy Bypass -File packaging/build.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\build" packaging\GeLauncher.spec

Write-Host ""
Write-Host "打包完成：$root\dist\GeLauncher\GeLauncher.exe"
