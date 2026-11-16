# 把 GitHub 仓库的「门面」一次配好：简介、主页、topics。
#
# 为什么：仓库的 description / topics / homepage 为空时，GitHub 搜索、推荐和
# 「Explore」都不会带上你；这三项是搜索流量的入口，配置一次就长期生效。
#
# 用法（需要 GitHub CLI）：
#   winget install GitHub.cli
#   gh auth login
#   powershell -ExecutionPolicy Bypass -File scripts\setup-github.ps1
#
# 社交预览图（1280×640）GitHub API 改不了，要手动上传：
#   仓库 → Settings → Social preview → 上传 docs/images/social-preview.png
#
# 没有 gh 时，也可以照抄下面的值手动填：仓库首页右上角「⚙ Settings」→
# Description / Website / Topics。

param(
    [string]$Repo = "rushansishui/Shotwright",
    [string]$Homepage = ""
)

$ErrorActionPreference = "Stop"

$Description = "Local-first AI short-video studio: script -> shots -> per-shot visuals, voice, subtitles, music, incremental render. MIT."
$Topics = @(
    "ai-video",
    "short-video",
    "text-to-video",
    "storyboard",
    "video-generation",
    "ffmpeg",
    "local-first",
    "fastapi",
    "react",
    "ecommerce",
    "tiktok",
    "mit"
)

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "没有找到 gh（GitHub CLI）。装一下：" -ForegroundColor Yellow
    Write-Host "  winget install GitHub.cli"
    Write-Host "  gh auth login"
    Write-Host ""
    Write-Host "或者手动填：GitHub 仓库页 → Settings → Description / Website / Topics"
    Write-Host "Description: $Description"
    Write-Host "Topics: $($Topics -join ', ')"
    exit 1
}

gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "gh 还没登录。先运行：gh auth login"
    exit 1
}

Write-Host "设置简介……"
gh repo edit $Repo --description $Description
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Homepage) {
    Write-Host "设置主页：$Homepage"
    gh repo edit $Repo --homepage $Homepage
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "添加 topics……"
foreach ($topic in $Topics) {
    gh repo edit $Repo --add-topic $topic
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "完成。还差一步：Settings → Social preview 上传 docs/images/social-preview.png" -ForegroundColor Green