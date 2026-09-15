$ErrorActionPreference = "Stop"
$ProjectPath = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) { throw "Virtual environment not found: $PythonExe" }
Set-Location $ProjectPath
Write-Warning "LAN mode exposes the dashboard to devices on your local network with no authentication. Do NOT port-forward this service to the public internet."
Write-Host "Starting on 0.0.0.0:8765" -ForegroundColor Cyan
& $PythonExe -m uvicorn src.ui_server:app --host 0.0.0.0 --port 8765
