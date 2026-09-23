# Stops the candly API and dashboard started by start.ps1 (or any uvicorn/Vite of this repo on :8000/:5173),
# including the child python of the venv launcher.
#
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1

. (Join-Path $PSScriptRoot "common.ps1")

$stopped = Stop-Candly
if ($stopped.Count -eq 0) {
    Write-Host "candly is not running"
} else {
    Write-Host "Stopped candly (processes $($stopped -join ', '))"
}
foreach ($port in @($ApiPort, $WebPort)) {
    if (-not (Wait-PortFree $port 10)) {
        $owners = (Get-ListenerIds $port) | ForEach-Object { "$_ ($((Get-Proc $_).Name))" }
        Write-Host "Port $port is still used by another program: $($owners -join ', ')" -ForegroundColor Yellow
    }
}
