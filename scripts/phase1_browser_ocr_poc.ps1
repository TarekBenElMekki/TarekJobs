param(
    [Parameter(Mandatory=$true)]
    [string]$Url
)

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
$PY = Join-Path $ROOT ".venv\Scripts\python.exe"

if (!(Test-Path $PY)) {
    throw "TTEA virtual environment not found."
}

Write-Host ""
Write-Host "TTEA Phase 1 POC" -ForegroundColor Cyan
Write-Host "URL: $Url" -ForegroundColor Gray
Write-Host ""

& $PY "$ROOT\src\phase1\browser_ocr_poc.py" $Url

if ($LASTEXITCODE -ne 0) {
    throw "Phase 1 POC failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "POC finished successfully." -ForegroundColor Green
Write-Host ""
