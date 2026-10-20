@echo off
setlocal
cd /d "%~dp0"
rem Stop the Shotwright server that is listening on port 8000, then start it again.
set FOUND=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do (
  set FOUND=1
  echo [Shotwright] Stopping the running server ^(PID %%p^)...
  taskkill /PID %%p /F >nul 2>nul
)
if not defined FOUND echo [Shotwright] No running server found on port 8000.
timeout /t 2 /nobreak >nul
call "%~dp0start.bat"
