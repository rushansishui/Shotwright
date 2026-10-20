@echo off
rem Stop the running Shotwright server (and its old window), then start it again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart.ps1"
