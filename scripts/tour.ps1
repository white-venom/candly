# Opens a visible Edge window that tours the candly dashboard on its own. Start candly first (start.ps1).
#   powershell -ExecutionPolicy Bypass -File scripts\tour.ps1            (one pass)
#   powershell -ExecutionPolicy Bypass -File scripts\tour.ps1 -Loop      (repeat until you close the window)
param([switch]$Loop)

. (Join-Path $PSScriptRoot "common.ps1")

$tourDir = Join-Path $Root "tools\tour"
if (-not (Test-Path (Join-Path $tourDir "node_modules\playwright-core"))) {
    Write-Host "Installing the tour helper (playwright-core, uses your installed Edge)..."
    Push-Location $tourDir
    npm install --no-fund --no-audit --loglevel=error
    Pop-Location
}
if (-not (Test-Web)) {
    Write-Host "The dashboard isn't running. Start it first: powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Red
    exit 1
}
Push-Location $tourDir
try {
    if ($Loop) { node tour.mjs --url $WebUrl --loop } else { node tour.mjs --url $WebUrl }
} finally {
    Pop-Location
}
