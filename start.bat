@echo off
REM Talk2Data Windows 1-Click Launcher
setlocal
cd /d "%~dp0"

echo ==========================================================
echo Starting Talk2Data Enterprise Analytics Platform...
echo ==========================================================

if exist ".venv\Scripts\python.exe" (
    echo Using virtual environment: .venv
    ".venv\Scripts\python.exe" run.py %*
) else (
    echo Virtual environment not found. Using system python...
    python run.py %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with code %ERRORLEVEL%.
    pause
)
