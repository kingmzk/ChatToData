# Talk2Data 1-Click PowerShell Launcher
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Detect project root directory
if (Test-Path (Join-Path $ScriptDir "run.py")) {
    $RootDir = $ScriptDir
} elseif (Test-Path (Join-Path $ScriptDir "..\run.py")) {
    $RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
} else {
    $RootDir = $ScriptDir
}

Set-Location $RootDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Starting Talk2Data Enterprise Analytics Platform..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $RootDir ".venv\Scripts\python.exe"
$RunScript = Join-Path $RootDir "run.py"

if (Test-Path $PythonExe) {
    Write-Host "Using virtual environment: .venv" -ForegroundColor Green
    & $PythonExe $RunScript $args
} else {
    Write-Host "Virtual environment not found. Using system python..." -ForegroundColor Yellow
    & python $RunScript $args
}
