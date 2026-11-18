@echo off
rem Windows: double-click to start.
if exist "%~dp0ktx_reserve\launch.bat" (
  call "%~dp0ktx_reserve\launch.bat"
) else (
  call "%~dp0launch.bat"
)
