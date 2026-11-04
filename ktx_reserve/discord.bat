@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  exit /b 2
)
".venv\Scripts\python.exe" -B discord_notify.py %*
exit /b %errorlevel%
