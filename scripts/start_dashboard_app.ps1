$ErrorActionPreference = "Stop"

$ProjectPath  = Split-Path -Parent $PSScriptRoot
$ServerScript = Join-Path $ProjectPath "scripts\start_dashboard.ps1"
$Url = "http://127.0.0.1:8765"

Set-Location $ProjectPath

$AlreadyRunning = $false
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 1
    $AlreadyRunning = ($r.StatusCode -eq 200)
} catch {}

if (-not $AlreadyRunning) {
    Start-Process powershell.exe -WorkingDirectory $ProjectPath -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-NoExit",
        "-File", $ServerScript
    )

    for ($i = 0; $i -lt 40; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 1
            if ($r.StatusCode -eq 200) {
                $AlreadyRunning = $true
                break
            }
        } catch {}
        Start-Sleep -Milliseconds 350
    }
}

if (-not $AlreadyRunning) {
    throw "The dashboard server did not become ready at $Url."
}

$EdgeCandidates = @(
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)

$ChromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
)

$BrowserExe = (
    $EdgeCandidates + $ChromeCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1
)

if ($BrowserExe) {
    Start-Process $BrowserExe -ArgumentList "--app=$Url"
} else {
    Start-Process $Url
}