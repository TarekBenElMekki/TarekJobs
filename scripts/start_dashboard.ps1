$ErrorActionPreference = "Stop"
$ProjectPath = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectPath ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found: $PythonExe"
}

Set-Location $ProjectPath
Write-Host "TTEA dashboard: http://127.0.0.1:8765" -ForegroundColor Cyan
& $PythonExe -m uvicorn src.ui_server:app --host 127.0.0.1 --port 8765