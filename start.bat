@echo off
rem ==============================================================
rem  DeskPet launcher  (bootstrapper only)
rem
rem  IMPORTANT: keep this file ASCII-ONLY.
rem  cmd.exe parses .bat files using the system OEM codepage
rem  (GBK/936 on a Chinese Windows). If the file contains UTF-8
rem  non-ASCII bytes, the mis-decoded multi-byte sequences also
rem  swallow the following quote / newline / percent characters,
rem  which corrupts the whole script structure and produces
rem  garbage errors like  'orlevel' is not recognized.
rem
rem  All real work (venv, dependencies, launch, error dialog)
rem  lives in launch.py, which is UTF-8 safe and shows proper
rem  Chinese messages through MessageBoxW.
rem ==============================================================

setlocal
cd /d "%~dp0"
set "PY="

rem --- 1) prefer the project virtual environment -----------------
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

rem --- 2) common per-user python.org install locations -----------
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

rem --- 3) the py launcher ---------------------------------------
if not defined PY (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)

rem --- 4) python on PATH ----------------------------------------
if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo.
    echo   Python 3.11 or newer was not found.
    echo.
    echo   Please install it from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup,
    echo   then run start.bat again.
    echo.
    pause
    exit /b 1
)

%PY% "%~dp0launch.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo   Launch failed ^(exit code %RC%^).
    echo   See data\logs\launch.log for details.
    echo.
    pause
)
endlocal
exit /b %RC%
