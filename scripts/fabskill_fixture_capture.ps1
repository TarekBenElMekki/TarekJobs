param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("search","job")]
    [string]$Type,

    [string]$JobId
)

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
$PY = Join-Path $ROOT ".venv\Scripts\python.exe"

if (!(Test-Path $PY)) {
    throw "TTEA Python environment not found: $PY"
}

if ($Type -eq "job" -and [string]::IsNullOrWhiteSpace($JobId)) {
    throw "JobId is required when Type=job."
}

$Script = Join-Path $ROOT "src\phase1\fabskill_fixture_capture.py"

if ($Type -eq "search") {

    & $PY $Script search

}
elseif ($Type -eq "job") {

    & $PY $Script job $JobId

}

if ($LASTEXITCODE -ne 0) {
    throw "Fabskill fixture capture failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Fabskill fixture capture completed successfully." -ForegroundColor Green
Write-Host ""
