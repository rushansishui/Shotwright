# 打包 Windows 安装包：构建网页界面 → 准备 FFmpeg → 用 PyInstaller 冻结（scripts\freeze.py）
# → 冒烟测试（scripts\smoke_test.py）→ 用 Inno Setup 生成安装程序 dist\installer\Shotwright-<版本>-setup.exe。
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
#   可选参数：
#     -FfmpegDir C:\ffmpeg\bin   用已有的 FFmpeg（目录里要有 ffmpeg.exe 和 ffprobe.exe），不下载
#     -NoAsr                     不带本地语音识别（安装包小约 300MB，剪辑页的语音识别不可用）
#     -SkipSmoke                 跳过冒烟测试
#     -IsccPath C:\x\ISCC.exe  指定 Inno Setup 编译器（默认在 PATH 和常见安装位置里找）
#
# FFmpeg 固定用下面 $FfmpegVersion 这个版本（gyan.dev 的 essentials 构建，带字幕用的 libass），
# 下载后校验 SHA256，保证同一个版本号打出来的安装包一样。升级 FFmpeg 时同时改版本号和校验值。
#
# 需要预先安装：Python 3.11–3.13（最新版 Python 可能还装不上本地语音识别的依赖）、Node.js 20+、Inno Setup 6.5+（https://jrsoftware.org/isdl.php；
# 低于 6.5 时安装界面没有简体中文）。
# GitHub Actions 上由 .github/workflows/release-windows.yml 调用。

param(
    [string]$FfmpegDir = '',
    [string]$IsccPath = '',
    [switch]$NoAsr,
    [switch]$SkipSmoke
)

$FfmpegVersion = '9.0.2'
$FfmpegSha256 = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
$FfmpegUrls = @(
    "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersion/ffmpeg-$FfmpegVersion-essentials_build.zip",
    "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$FfmpegVersion-essentials_build.zip"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest 显示进度条时下载会慢很多
# 打包过程中运行的 Python（freeze.py、pip-licenses、冒烟测试）一律用 UTF-8：GitHub 的 Windows 机器
# 和英文系统的默认编码是 cp1252，打印中文会报错。控制台也按 UTF-8 解读它们的输出，日志里中文不乱码。
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked {
    # 外部命令失败时 PowerShell 不会自动停下，这里检查退出码。
    # 故意不用 param()：这样 -m、-hide_banner 这类参数会原样传给外部命令。
    # $rest 用 Select-Object 取成数组：只剩一个参数时，`$exe, $rest = $args` 得到的是字符串，
    # 展开传参时会被拆成单个字符（ci 变成 c i）。
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') 失败，退出码 $LASTEXITCODE" }
}

$version = (Select-String -Path 'pyproject.toml' -Pattern '^version\s*=\s*"(.+)"').Matches[0].Groups[1].Value
Write-Host "[build] Shotwright $version"

# ---- Python 环境 -------------------------------------------------------------
# 本地语音识别依赖的 onnxruntime 只给较早的 Python 版本提供安装包，最新版 Python 往往还装不上，
# 所以打包用的 Python 固定在下面这几个版本里选（优先 3.12）。不能直接用 `py -3`：它会挑机器上
# 最新的 Python，GitHub 的 Windows 机器上预装了更新的版本。
$SupportedPython = @('3.12', '3.13', '3.11')

function Get-PythonVersion {
    # 返回 "3.12" 这样的版本号；命令不存在或运行失败时返回空。
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    try {
        $out = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0) { return "$out".Trim() }
    } catch { }
    return ''
}

$py = '.venv-build\Scripts\python.exe'
if (Test-Path $py) {
    $venvVersion = Get-PythonVersion $py
    if ($SupportedPython -notcontains $venvVersion) {
        Write-Host "[build] .venv-build 用的是 Python $venvVersion，不在支持的版本里，重新创建"
        Remove-Item .venv-build -Recurse -Force
    }
}
if (-not (Test-Path $py)) {
    # 候选：PATH 里的 python（GitHub Actions 上就是 setup-python 装的那个），再按版本问 py 启动器。
    $candidates = @()
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @('python') }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in $SupportedPython) { $candidates += , @('py', "-$v") }
    }
    $base = $null
    foreach ($c in $candidates) {
        if ($SupportedPython -contains (Get-PythonVersion @c)) { $base = $c; break }
    }
    if (-not $base) {
        throw "找不到 Python $($SupportedPython -join ' / ')，请从 https://www.python.org/downloads/ 安装其中一个"
    }
    Write-Host "[build] 用 Python $(Get-PythonVersion @base)（$($base -join ' ')）创建打包用的虚拟环境 .venv-build"
    Invoke-Checked @base -m venv .venv-build
}
$extra = if ($NoAsr) { '.[desktop]' } else { '.[asr,desktop]' }
Write-Host "[build] 安装依赖 $extra + pyinstaller + pip-licenses"
Invoke-Checked $py -m pip install --upgrade pip
Invoke-Checked $py -m pip install $extra pyinstaller pip-licenses

# ---- 网页界面 ----------------------------------------------------------------
Write-Host '[build] 构建网页界面'
Push-Location web
try {
    if (-not (Test-Path 'node_modules')) { Invoke-Checked npm.cmd ci }
    Invoke-Checked npm.cmd run build
} finally { Pop-Location }

# ---- FFmpeg ------------------------------------------------------------------
if (-not $FfmpegDir) {
    # 缓存目录带版本号：升级 FFmpeg 后不会误用旧版本的缓存。
    $cache = Join-Path $root "build\ffmpeg-$FfmpegVersion"
    $found = Get-ChildItem $cache -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        New-Item -ItemType Directory -Force $cache | Out-Null
        $zip = Join-Path $cache 'ffmpeg.zip'
        $downloaded = $false
        foreach ($url in $FfmpegUrls) {
            try {
                Write-Host "[build] 下载 FFmpeg $FfmpegVersion：$url"
                Invoke-WebRequest -Uri $url -OutFile $zip
                $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
                if ($hash -ne $FfmpegSha256) { throw "SHA256 不符：$hash" }
                $downloaded = $true
                break
            } catch {
                Write-Warning "下载失败：$_"
                Remove-Item $zip -ErrorAction SilentlyContinue
            }
        }
        if (-not $downloaded) { throw 'FFmpeg 下载失败，可以用 -FfmpegDir 指定本机已有的 FFmpeg' }
        Expand-Archive -Path $zip -DestinationPath $cache -Force
        Remove-Item $zip
        $found = Get-ChildItem $cache -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    }
    $FfmpegDir = $found.DirectoryName
    # 许可证和写着源码地址的 README.txt 在 bin 的上一级，复制过来一起打包（FFmpeg 是 GPL 授权）。
    $parent = Split-Path $FfmpegDir -Parent
    $license = Get-ChildItem $parent -Filter 'LICENSE*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $FfmpegDir 'LICENSE.txt') -Force }
    $readme = Join-Path $parent 'README.txt'
    if (Test-Path $readme) { Copy-Item $readme (Join-Path $FfmpegDir 'README.txt') -Force }
}
Write-Host "[build] FFmpeg：$FfmpegDir"
Invoke-Checked (Join-Path $FfmpegDir 'ffmpeg.exe') -hide_banner -version

# ---- 冻结 --------------------------------------------------------------------
Write-Host '[build] PyInstaller 冻结'
Invoke-Checked $py scripts\freeze.py --ffmpeg-dir $FfmpegDir

# 打包进去的第三方 Python 库大多要求随程序附上许可证，生成一份放在程序目录里。
# 只用于打包的工具（pip-licenses 和它的依赖）不随程序分发，排除掉。
Write-Host '[build] 生成第三方许可证清单'
Invoke-Checked $py -m piplicenses --format=plain-vertical --with-license-file --no-license-path --with-urls `
    --ignore-packages shotwright pip pip-licenses prettytable wcwidth `
    --output-file dist\Shotwright\THIRD_PARTY_LICENSES.txt

if (-not $SkipSmoke) {
    Write-Host '[build] 冒烟测试'
    Invoke-Checked $py scripts\smoke_test.py dist\Shotwright\Shotwright.exe
}

# ---- 安装程序 ----------------------------------------------------------------
Write-Host '[build] 生成安装程序'
$iscc = @(
    $IsccPath,
    (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw '找不到 Inno Setup 6（ISCC.exe），请从 https://jrsoftware.org/isdl.php 安装'
}
$isccVersion = $null
try { $isccVersion = [version](Get-Item $iscc).VersionInfo.ProductVersion.Split(' ')[0] } catch { }
Write-Host "[build] Inno Setup $isccVersion：$iscc"
if ($isccVersion -and $isccVersion -lt [version]'6.5') { Write-Warning 'Inno Setup 低于 6.5，安装界面不会有简体中文' }
Invoke-Checked $iscc "/DAppVersion=$version" 'scripts\installer.iss'

$setup = "dist\installer\Shotwright-$version-setup.exe"
$size = [math]::Round((Get-Item $setup).Length / 1MB)
Write-Host "[build] 完成：$setup（$size MB）"
