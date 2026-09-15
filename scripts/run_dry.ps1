$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtual environment not found at $Python"
}
Push-Location $Root
try {
    & $Python -m src.main dry-run --limit 100
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}