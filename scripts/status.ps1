# Prints a summary of the running candly API: data source, Fyers login, ingest, Fyers sync, expiry check.
#
#   powershell -ExecutionPolicy Bypass -File scripts\status.ps1

. (Join-Path $PSScriptRoot "common.ps1")

function Show([string]$Label, [string]$Value) {
    Write-Host ("{0,-14}{1}" -f $Label, $Value)
}

try {
    $health = Get-Json "$ApiUrl/api/health"
} catch {
    Show "API" "not running at $ApiUrl (start it with scripts\start.ps1)"
    exit 1
}

if (Test-Web) { $web = "running" } else { $web = "not running" }
Show "API" "running at $ApiUrl (version $($health.version))"
Show "Dashboard" "$WebUrl ($web)"
Show "Data source" $health.data_source

if (-not $health.keys.fyers) {
    $fyers = "keys not set (add FYERS_APP_ID and FYERS_SECRET_KEY to .env)"
} elseif ($health.keys.fyers_connected) {
    $login = Get-Json "$ApiUrl/api/auth/fyers/status"
    $fyers = "connected, session valid until " + (Format-Ist $login.expires_at)
} else {
    $fyers = "not connected (click Connect Fyers in the dashboard)"
}
Show "Fyers" $fyers

if ($null -eq $health.ingest -or $health.ingest.status -eq "ok") {
    Show "Ingest" "ok"
} else {
    Show "Ingest" "blocked: $($health.ingest.reason)"
}
Show "Sync" (Format-Sync $health.sync)

$check = $health.expiry_check
if ($null -eq $check) {
    Show "Expiry check" "-"
} else {
    Show "Expiry check" "$($check.status) (checked $(Format-Ist $check.checked_at))"
    foreach ($m in $check.mismatches) {
        Show "" "$($m.instrument): rules say $($m.rules), the exchange lists $($m.exchange)"
    }
}
$open = @($health.markets | ForEach-Object { "$($_.exchange) $($_.phase)" })
Show "Markets" ($open -join ", ")
