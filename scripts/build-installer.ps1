# 打包 Windows 安装包：构建网页界面 → 准备 FFmpeg → 用 PyInstaller 冻结（scripts\freeze.py）
# → 冒烟测试（scripts\smoke_test.py）→ 用 Inno Setup 生成安装程序 dist\installer\Shotwright-<版本>-setup.exe。
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
#   可选参数：
#     -FfmpegDir C:\ffmpeg\bin   用已有的 FFmpeg（目录里要有 ffmpeg.exe 和 ffprobe.exe），不下载
#     -NoAsr                     不带本地语音识别（安装包小约 300MB，剪辑页的语音识别不可用）
#     -SkipSmoke                 跳过冒烟测试
#
# 需要预先安装：Python 3.11+、Node.js 20+、Inno Setup 6（https://jrsoftware.org/isdl.php）。
# GitHub Actions 上由 .github/workflows/release-windows.yml 调用。

param(
    [string]$FfmpegDir = '',
    [string]$FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    [switch]$NoAsr,
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest 显示进度条时下载会慢很多
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked {
    # 外部命令失败时 PowerShell 不会自动停下，这里检查退出码。
    # 故意不用 param()：这样 -m、-hide_banner 这类参数会原样传给外部命令。
    $exe, $rest = $args
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') 失败，退出码 $LASTEXITCODE" }
}

$version = (Select-String -Path 'pyproject.toml' -Pattern '^version\s*=\s*"(.+)"').Matches[0].Groups[1].Value
Write-Host "[build] Shotwright $version"

# ---- Python 环境 -------------------------------------------------------------
$py = '.venv-build\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host '[build] 创建打包用的虚拟环境 .venv-build'
    if (Get-Command py -ErrorAction SilentlyContinue) { Invoke-Checked py -3 -m venv .venv-build }
    else { Invoke-Checked python -m venv .venv-build }
}
$extra = if ($NoAsr) { '.' } else { '.[asr]' }
Write-Host "[build] 安装依赖 $extra + pyinstaller"
Invoke-Checked $py -m pip install --upgrade pip
Invoke-Checked $py -m pip install $extra pyinstaller

# ---- 网页界面 ----------------------------------------------------------------
Write-Host '[build] 构建网页界面'
Push-Location web
try {
    if (-not (Test-Path 'node_modules')) { Invoke-Checked npm.cmd ci }
    Invoke-Checked npm.cmd run build
} finally { Pop-Location }

# ---- FFmpeg ------------------------------------------------------------------
if (-not $FfmpegDir) {
    $cache = Join-Path $root 'build\ffmpeg'
    $found = Get-ChildItem $cache -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        New-Item -ItemType Directory -Force $cache | Out-Null
        $zip = Join-Path $cache 'ffmpeg.zip'
        # 第一个地址下载失败时换 GitHub 上的构建（同样带字幕用的 libass）。
        $urls = @($FfmpegUrl, 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip')
        $downloaded = $false
        foreach ($url in $urls) {
            try {
                Write-Host "[build] 下载 FFmpeg：$url"
                Invoke-WebRequest -Uri $url -OutFile $zip
                $downloaded = $true
                break
            } catch { Write-Warning "下载失败：$_" }
        }
        if (-not $downloaded) { throw 'FFmpeg 下载失败，可以用 -FfmpegDir 指定本机已有的 FFmpeg' }
        Expand-Archive -Path $zip -DestinationPath $cache -Force
        Remove-Item $zip
        $found = Get-ChildItem $cache -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    }
    $FfmpegDir = $found.DirectoryName
    # 许可证文件在 bin 的上一级，复制过来一起打包。
    $license = Get-ChildItem (Split-Path $FfmpegDir -Parent) -Filter 'LICENSE*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $FfmpegDir 'LICENSE.txt') -Force }
}
Write-Host "[build] FFmpeg：$FfmpegDir"
Invoke-Checked (Join-Path $FfmpegDir 'ffmpeg.exe') -hide_banner -version

# ---- 冻结 --------------------------------------------------------------------
Write-Host '[build] PyInstaller 冻结'
Invoke-Checked $py scripts\freeze.py --ffmpeg-dir $FfmpegDir

if (-not $SkipSmoke) {
    Write-Host '[build] 冒烟测试'
    Invoke-Checked $py scripts\smoke_test.py dist\Shotwright\Shotwright.exe
}

# ---- 安装程序 ----------------------------------------------------------------
Write-Host '[build] 生成安装程序'
$iscc = @(
    (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw '找不到 Inno Setup 6（ISCC.exe），请从 https://jrsoftware.org/isdl.php 安装'
}
Invoke-Checked $iscc "/DAppVersion=$version" 'scripts\installer.iss'

$setup = "dist\installer\Shotwright-$version-setup.exe"
$size = [math]::Round((Get-Item $setup).Length / 1MB)
Write-Host "[build] 完成：$setup（$size MB）"
