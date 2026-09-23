# Starts candly in the background: the API (uvicorn on 127.0.0.1:8000, scheduler on) and the dashboard
# (Vite dev server on :5173), as hidden processes. PIDs go to data\run, logs to data\logs.
# candly processes that are already running are restarted.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1

. (Join-Path $PSScriptRoot "common.ps1")

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

$stopped = Stop-Candly
if ($stopped.Count -gt 0) { Write-Host "Restarting: stopped the running candly processes ($($stopped -join ', '))" }
foreach ($port in @($ApiPort, $WebPort)) {
    if (-not (Wait-PortFree $port)) {
        $owners = (Get-ListenerIds $port) | ForEach-Object { "$_ ($((Get-Proc $_).Name))" }
        Write-Host "Port $port is used by another program: $($owners -join ', '). Close it and run this again." -ForegroundColor Red
        exit 1
    }
}

$python = Join-Path $Root "backend\.venv\Scripts\python.exe"
$vite = Join-Path $Root "frontend\node_modules\vite\bin\vite.js"
$node = Get-Command node.exe -ErrorAction SilentlyContinue
foreach ($path in @($python, $vite)) {
    if (-not (Test-Path $path)) { Write-Host "Missing $path" -ForegroundColor Red; exit 1 }
}
if ($null -eq $node) { Write-Host "node.exe not found; install Node.js LTS" -ForegroundColor Red; exit 1 }

function Get-FreshLog([string]$Name) {
    # Keeps the previous run's log as <name>.prev.
    $path = Join-Path $LogDir $Name
    if (Test-Path $path) { Move-Item $path "$path.prev" -Force -ErrorAction SilentlyContinue }
    return $path
}

$env:SCHEDULER_ENABLED = "true"
$env:PYTHONUNBUFFERED = "1"
# ArgumentList is a single string on purpose: PowerShell 5.1 joins an array without quoting its items.
$api = Start-Process -FilePath $python -ArgumentList "-m uvicorn candly.api.app:app --host 127.0.0.1 --port $ApiPort" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Get-FreshLog "api.out.log") -RedirectStandardError (Get-FreshLog "api.err.log")
Set-Content -Path (Join-Path $RunDir "api.pid") -Value $api.Id -Encoding Ascii

$web = Start-Process -FilePath $node.Source -ArgumentList "`"$vite`" --port $WebPort --strictPort" `
    -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Get-FreshLog "web.out.log") -RedirectStandardError (Get-FreshLog "web.err.log")
Set-Content -Path (Join-Path $RunDir "web.pid") -Value $web.Id -Encoding Ascii

Write-Host "Waiting for the API..."
$health = $null
$deadline = (Get-Date).AddSeconds(90)
while ($null -eq $health) {
    try { $health = Get-Json "$ApiUrl/api/health" 5 } catch { $health = $null }
    if ($null -ne $health) { break }
    if ($api.HasExited) {
        Write-Host "The API stopped during startup. Last lines of data\logs\api.err.log:" -ForegroundColor Red
        Get-Content (Join-Path $LogDir "api.err.log") -Tail 20
        exit 1
    }
    if ((Get-Date) -gt $deadline) {
        Write-Host "The API did not answer within 90 s; see data\logs\api.err.log" -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Milliseconds 500
}

$webUp = $false
$deadline = (Get-Date).AddSeconds(60)
while (-not $webUp -and -not $web.HasExited -and (Get-Date) -lt $deadline) {
    $webUp = Test-Web
    if (-not $webUp) { Start-Sleep -Milliseconds 500 }
}

if ($health.keys.fyers_connected) { $fyers = "connected" } else { $fyers = "not connected" }
Write-Host ""
Write-Host "candly is running" -ForegroundColor Green
if ($webUp) {
    Write-Host "  Dashboard  $WebUrl"
} else {
    Write-Host "  Dashboard  not answering yet; see data\logs\web.err.log" -ForegroundColor Yellow
}
Write-Host "  API        $ApiUrl/api/health"
Write-Host "  Data       $($health.data_source) (Fyers $fyers); sync: $(Format-Sync $health.sync)"
Write-Host "  Logs       data\logs\api.*.log, data\logs\web.*.log, data\logs\candly.log"
Write-Host "  Status     scripts\status.ps1    Stop  scripts\stop.ps1"
