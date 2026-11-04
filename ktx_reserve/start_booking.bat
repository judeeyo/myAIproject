@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
rem Edit the date, time range, and passenger count below if needed.
call run.bat --dep 서울 --arr 동대구 --date 2026-11-06 --from 13:49 --to 13:58 --adult 2 --seat general-only --interval-min 1 --interval-max 2 --pay
set "KTX_EXIT_CODE=%errorlevel%"
pause
exit /b %KTX_EXIT_CODE%
