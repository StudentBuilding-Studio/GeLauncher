# 打包成 dist/GeLauncher/（目录分发）
# 用法：在仓库根目录执行  powershell -ExecutionPolicy Bypass -File packaging/build.ps1
#
# 可选代码签名（需要自备证书）：
#   $env:GE_SIGN_PFX = "C:\path\to\cert.pfx"
#   $env:GE_SIGN_PASSWORD = "pfx 密码"
# 设置后会在打包结束时自动用 signtool 签名。说明见 packaging/signing.md。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\build" packaging\GeLauncher.spec

$exe = Join-Path $root "dist\GeLauncher\GeLauncher.exe"

if ($env:GE_SIGN_PFX -and (Test-Path $env:GE_SIGN_PFX)) {
    $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue)
    if ($null -eq $signtool) {
        Write-Warning "找不到 signtool.exe（装 Windows SDK 后重试），跳过签名"
    } else {
        Write-Host "正在签名 $exe ..."
        & $signtool.Path sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
            /f $env:GE_SIGN_PFX /p $env:GE_SIGN_PASSWORD $exe
        Write-Host "签名完成"
    }
}

Write-Host ""
Write-Host "打包完成：$exe"
