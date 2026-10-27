@echo off
setlocal
cd /d "%~dp0"
title Shotwright

set MISSING=
where ffmpeg >nul 2>nul || (set "MISSING=1" & echo [Shotwright] Missing FFmpeg.   Install: winget install Gyan.FFmpeg)
where py >nul 2>nul || where python >nul 2>nul || (set "MISSING=1" & echo [Shotwright] Missing Python 3.11+.   Install: winget install Python.Python.3.12)
where npm >nul 2>nul || (echo [Shotwright] Node.js not found, the web UI cannot be built.   Install: winget install OpenJS.NodeJS.LTS)
if defined MISSING (
  echo [Shotwright] Install the missing tools, open a NEW window, then run start.bat again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [Shotwright] Creating Python virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo [Shotwright] Python 3.11+ not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
  )
)

rem Reinstall dependencies whenever pyproject.toml changes (new versions may add packages).
fc /b "pyproject.toml" ".venv\.installed" >nul 2>nul
if errorlevel 1 (
  echo [Shotwright] Installing Python dependencies...
  ".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :fail
  copy /y "pyproject.toml" ".venv\.installed" >nul
)

if not exist "config.toml" (
  copy /y "config.example.toml" "config.toml" >nul
  echo [Shotwright] Created config.toml from the template. Fill in API keys there when needed.
)

rem Rebuild the web UI when it is missing or any file under web\src is newer than the build
rem (for example after pulling new code), so the browser never shows an outdated UI.
set "NEED_WEB="
if not exist "web\dist\index.html" set "NEED_WEB=1"
if not defined NEED_WEB (
  powershell -NoProfile -Command "$d=(Get-Item 'web\dist\index.html').LastWriteTime; $n=Get-ChildItem 'web\src','web\index.html','web\package.json' -Recurse -File | Where-Object { $_.LastWriteTime -gt $d } | Select-Object -First 1; if ($n) { exit 1 }"
  if errorlevel 1 set "NEED_WEB=1"
)
if defined NEED_WEB (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [Shotwright] Node.js not found, skipping the web UI build. Install Node.js 20+ from https://nodejs.org/
  ) else (
    echo [Shotwright] Building the web UI...
    pushd web
    call npm install || (popd & goto :fail)
    call npm run build || (popd & goto :fail)
    popd
  )
)

rem An older Shotwright server from this folder is still running: don't start a second one.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\running.ps1" >nul 2>nul
if not errorlevel 1 (
  echo [Shotwright] Shotwright is already running - an older window is probably still open.
  echo [Shotwright] Close that window first ^(or run restart.bat^), then run start.bat again.
  pause
  exit /b 1
)

rem Port 8000 is preferred; if Windows has reserved it or another program uses it, the next free port is used.
echo [Shotwright] Starting... the browser opens automatically  (press Ctrl+C to stop)
".venv\Scripts\shotwright.exe" serve --open
pause
exit /b 0

:fail
echo [Shotwright] Setup failed, see the messages above.
pause
exit /b 1
