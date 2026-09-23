# Shared by start.ps1, stop.ps1 and status.ps1 (dot-sourced). Windows PowerShell 5.1, no admin rights needed.

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:Path = "C:\Program Files\Git\cmd;$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\nodejs;" + $env:Path
$RunDir = Join-Path $Root "data\run"
$LogDir = Join-Path $Root "data\logs"
$ApiPort = 8000
$WebPort = 5173
$ApiUrl = "http://127.0.0.1:$ApiPort"
$WebUrl = "http://localhost:$WebPort"
$ProgressPreference = "SilentlyContinue"  # the Invoke-WebRequest progress bar is very slow in 5.1

function Test-Contains([string]$Text, [string]$Part) {
    return $Text.IndexOf($Part, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-Ours($Proc) {
    # The API (uvicorn candly.api.app, the venv launcher included) or the Vite dev server of this repo.
    if ($null -eq $Proc) { return $false }
    $cmd = [string]$Proc.CommandLine
    return (Test-Contains $cmd "candly.api.app") -or (Test-Contains $cmd (Join-Path $Root "frontend"))
}

function Get-Proc([int]$Id) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction SilentlyContinue
}

function Get-ListenerIds([int]$Port) {
    $ids = @()
    foreach ($line in (netstat.exe -ano)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)") { $ids += [int]$Matches[1] }
    }
    return @($ids | Sort-Object -Unique)
}

function Stop-Tree([int]$Id, $All) {
    # Children first. A child must be younger than its parent: Windows reuses process ids.
    $parent = $All | Where-Object { $_.ProcessId -eq $Id } | Select-Object -First 1
    if ($null -eq $parent) { return }
    $kids = @($All | Where-Object {
        $_.ParentProcessId -eq $Id -and $_.ProcessId -ne $Id -and $_.CreationDate -ge $parent.CreationDate
    })
    foreach ($kid in $kids) { Stop-Tree $kid.ProcessId $All }
    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
}

function Stop-Candly {
    # Stops our API and Vite processes: those in data\run\*.pid and our listeners on the two ports.
    # Returns the ids it stopped. Other programs are never touched.
    $ids = @()
    foreach ($name in @("api", "web")) {
        $file = Join-Path $RunDir "$name.pid"
        if (Test-Path $file) {
            $id = 0
            if ([int]::TryParse([string](Get-Content $file -TotalCount 1), [ref]$id) -and (Test-Ours (Get-Proc $id))) {
                $ids += $id
            }
            Remove-Item $file -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($port in @($ApiPort, $WebPort)) {
        foreach ($id in (Get-ListenerIds $port)) {
            $proc = Get-Proc $id
            if (Test-Ours $proc) {
                $ids += $id
                $parent = Get-Proc $proc.ParentProcessId
                if (Test-Ours $parent) { $ids += $parent.ProcessId }  # the venv launcher of uvicorn
            }
        }
    }
    $ids = @($ids | Sort-Object -Unique)
    if ($ids.Count -gt 0) {
        $all = @(Get-CimInstance Win32_Process)
        foreach ($id in $ids) { Stop-Tree $id $all }
    }
    return $ids
}

function Wait-PortFree([int]$Port, [int]$Seconds = 15) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-ListenerIds $Port).Count -gt 0) {
        if ((Get-Date) -gt $deadline) { return $false }
        Start-Sleep -Milliseconds 300
    }
    return $true
}

function Get-Json([string]$Url, [int]$TimeoutSec = 10) {
    # Decoded as UTF-8 explicitly: 5.1 would read a JSON body without a charset as Latin-1.
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return ConvertFrom-Json ([System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()))
}

function Test-Web {
    try {
        $null = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

function Format-Ist($Epoch) {
    if ($null -eq $Epoch) { return "-" }
    $t = [DateTimeOffset]::FromUnixTimeSeconds([long]$Epoch).ToOffset([TimeSpan]::FromMinutes(330))
    return $t.ToString("yyyy-MM-dd HH:mm") + " IST"
}

function Format-Sync($Sync) {
    if ($null -eq $Sync) { return "-" }
    $text = [string]$Sync.status
    if ($Sync.status -eq "running") {
        $text += " {0:P0} ({1}): {2}" -f [double]$Sync.progress, $Sync.step, $Sync.message
    } elseif ($Sync.status -eq "error") {
        $text += ": " + $Sync.error
    } elseif ($Sync.status -eq "done") {
        $text += " at " + (Format-Ist $Sync.finished_at) + ": " + $Sync.message
    }
    return $text
}
