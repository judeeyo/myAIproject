@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo Existing .venv needs Python 3.11+. Rename it and run setup.bat again.
    exit /b 2
  )
  goto install
)
set "KTX_PYTHON="
for %%V in (3.15 3.14 3.13 3.12 3.11) do (
  py -%%V -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "KTX_PYTHON=py -%%V"
    goto found
  )
)
for %%P in (python python3) do (
  %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "KTX_PYTHON=%%P"
    goto found
  )
)
echo Install Python 3.11+ from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then reopen this terminal.
exit /b 2
:found
%KTX_PYTHON% --version
%KTX_PYTHON% -m venv .venv
if errorlevel 1 exit /b 1
:install
git --version >nul 2>&1
if errorlevel 1 (
  echo Install Git from https://git-scm.com/download/win and reopen this terminal.
  exit /b 2
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -B -m unittest discover -s tests -v
exit /b %errorlevel%
