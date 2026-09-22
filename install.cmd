@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if not %errorlevel%==0 (
        echo Python 3.10+ was not found in PATH.
        echo Install Python, then run this file again.
        pause
        exit /b 1
    )
    set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo.
echo Installed PC MCP successfully.
echo Next: run save-api-key.ps1, then configure-tunnel.ps1.
pause