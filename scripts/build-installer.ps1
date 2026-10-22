# 打包 Windows 安装包：构建网页界面 → 用 PyInstaller 冻结成单目录 → 用 Inno Setup 生成安装程序。
# 用法（在仓库根目录）：powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
#
# 需要预先安装：
#   - Node.js 20+（构建 web/dist）
#   - Inno Setup 6（iscc.exe，默认在 C:\Program Files (x86)\Inno Setup 6）
#   - FFmpeg 不打包进安装包：安装后由 start 脚本检查，缺失时提示 winget 安装。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path 'pyproject.toml' -Pattern '^version\s*=\s*"(.+)"').Matches[0].Groups[1].Value
Write-Host "[build] Shotwright $version"

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '[build] Creating virtual environment'
    py -3 -m venv .venv
}

Write-Host '[build] Installing build dependencies'
& '.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.venv\Scripts\python.exe' -m pip install -e . pyinstaller

Write-Host '[build] Building the web UI'
Push-Location web
if (-not (Test-Path 'node_modules')) { npm ci }
npm run build
Pop-Location

# 前端产物要随包分发，PyInstaller 通过 --add-data 带进去。
$sep = ';'  # Windows 下 --add-data 的分隔符
Write-Host '[build] Freezing with PyInstaller'
& '.venv\Scripts\pyinstaller.exe' `
    --noconfirm --clean --windowed --name Shotwright `
    --add-data "web/dist${sep}web/dist" `
    --add-data "resources${sep}resources" `
    --collect-all edge_tts `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    "--add-data=config.example.toml${sep}." `
    shotwright/__main__.py

Write-Host '[build] Building the installer'
$iscc = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw 'Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php'
}
& $iscc "/DAppVersion=$version" 'scripts\installer.iss'

Write-Host "[build] Done. Installer in dist\installer\"
