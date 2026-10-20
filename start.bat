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

if not exist ".venv\.installed" (
  echo [Shotwright] Installing Python dependencies...
  ".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :fail
  echo ok> ".venv\.installed"
)

if not exist "config.toml" (
  copy /y "config.example.toml" "config.toml" >nul
  echo [Shotwright] Created config.toml from the template. Fill in API keys there when needed.
)

if not exist "web\dist\index.html" (
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

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo [Shotwright] Port 8000 is already in use - an older Shotwright window is probably still running.
  echo [Shotwright] Close that window first ^(or press Ctrl+C in it^), then run start.bat again.
  pause
  exit /b 1
)

echo [Shotwright] Starting at http://127.0.0.1:8000  (press Ctrl+C to stop)
start "" "http://127.0.0.1:8000"
".venv\Scripts\shotwright.exe" serve
pause
exit /b 0

:fail
echo [Shotwright] Setup failed, see the messages above.
pause
exit /b 1
