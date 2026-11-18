@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
set "NEED_SETUP=0"
if not exist ".venv\Scripts\python.exe" set "NEED_SETUP=1"
if "%NEED_SETUP%"=="0" fc /b requirements.txt ".venv\.ktx-ready" >/dev/null 2>&1 || set "NEED_SETUP=1"
if "%NEED_SETUP%"=="0" goto run

set "MISSING=0"
if exist ".venv\Scripts\python.exe" goto checkgit
set "HAVE_PY=0"
for %%V in (3.15 3.14 3.13 3.12 3.11) do (
  py -%%V -c "import sys" >/dev/null 2>&1 && set "HAVE_PY=1"
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1 && set "HAVE_PY=1"
if "%HAVE_PY%"=="1" goto checkgit
echo Python 3.11+ is not installed. Installing Python 3.13 ...
winget install --id Python.Python.3.13 -e --source winget --accept-source-agreements --accept-package-agreements
if errorlevel 1 start "" "https://www.python.org/downloads/windows/"
set "MISSING=1"
:checkgit
git --version >/dev/null 2>&1
if not errorlevel 1 goto checked
echo Git is not installed. Installing Git ...
winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
if errorlevel 1 start "" "https://git-scm.com/download/win"
set "MISSING=1"
:checked
if "%MISSING%"=="1" (
  echo.
  echo Finish the installation, then double-click start.bat again.
  pause
  exit /b 1
)

echo First run: installing required packages. This takes 1-3 minutes, once.
call setup.bat
if errorlevel 1 (
  echo.
  echo Setup failed. Check the messages above.
  pause
  exit /b 1
)
copy /y requirements.txt ".venv\.ktx-ready" >nul

:run
echo Opening the settings screen. Closing this window stops the program.
call ui.bat
