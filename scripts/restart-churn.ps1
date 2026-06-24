param(
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptDir "..")
$FrontendDir = Join-Path $Root "frontend"
$Python = Join-Path $Root ".venv-win\Scripts\python.exe"
$BackendPort = 8000
$FrontendPort = 5176
$OldBackendPorts = @(8017)
$ManagedPorts = @($BackendPort, $FrontendPort) + $OldBackendPorts
$Stopped = New-Object System.Collections.Generic.HashSet[int]

function Write-Step($Message) {
    Write-Host "[churn-restart] $Message"
}

function Normalize-ProcessPathEnv {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if ([string]::IsNullOrWhiteSpace($pathValue)) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    if (-not [string]::IsNullOrWhiteSpace($pathValue)) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
        [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
    }
}

function Get-ProcessRows {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        Write-Warning "Could not inspect process command lines. Run PowerShell as Administrator if cleanup is incomplete."
        return @()
    }
}

function Get-ProcInfo([int]$ProcessId) {
    try {
        return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    } catch {
        return $null
    }
}

function Get-Listeners([int[]]$Ports) {
    $listeners = @()
    try {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $Ports -contains [int]$_.LocalPort } |
            ForEach-Object {
                [pscustomobject]@{
                    LocalPort = [int]$_.LocalPort
                    OwningProcess = [int]$_.OwningProcess
                    Source = "Get-NetTCPConnection"
                }
            })
    } catch {
        $listeners = @(netstat -ano |
            Select-String -Pattern "LISTENING" |
            ForEach-Object {
                $parts = ($_.Line.Trim() -split "\s+")
                if ($parts.Count -lt 5) { return }
                $local = $parts[1]
                $pidText = $parts[-1]
                $portText = ($local -split ":")[-1]
                $port = 0
                $parsedProcessId = 0
                if (-not [int]::TryParse($portText, [ref]$port)) { return }
                if (-not [int]::TryParse($pidText, [ref]$parsedProcessId)) { return }
                if ($Ports -contains $port) {
                    [pscustomobject]@{
                        LocalPort = $port
                        OwningProcess = $parsedProcessId
                        Source = "netstat"
                    }
                }
            })
    }
    return $listeners
}

function Stop-Pid([int]$ProcessId, [string]$Reason) {
    if ($ProcessId -le 0) { return }
    if ($Stopped.Contains($ProcessId)) { return }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        [void]$Stopped.Add($ProcessId)
        Write-Step "stopped PID $ProcessId ($Reason)"
    } catch {
        Write-Warning "Could not stop PID $ProcessId ($Reason): $($_.Exception.Message)"
    }
}

function Stop-OrphanWorkersForOwner([int]$OwnerPid) {
    $rows = Get-ProcessRows
    $pattern = "spawn_main(parent_pid=$OwnerPid,"
    $workers = @($rows | Where-Object {
        $cmd = ""
        if ($null -ne $_.CommandLine) { $cmd = [string]$_.CommandLine }
        $cmd -like "*$pattern*"
    })
    foreach ($worker in $workers) {
        Stop-Pid -ProcessId ([int]$worker.ProcessId) -Reason "orphaned uvicorn worker for stale parent $OwnerPid"
    }
}

function Stop-ProjectProcesses {
    $rows = Get-ProcessRows
    $rootText = [string]$Root

    $direct = @($rows | Where-Object {
        $cmd = ""
        if ($null -ne $_.CommandLine) { $cmd = [string]$_.CommandLine }
        ($_.Name -like "*python*" -or $_.Name -like "*node*") -and $cmd.Contains($rootText)
    })
    foreach ($proc in $direct) {
        Stop-Pid -ProcessId ([int]$proc.ProcessId) -Reason "Churn process"
    }

    $listeners = @(Get-Listeners -Ports $ManagedPorts)
    foreach ($listener in $listeners) {
        $ownerProcessId = [int]$listener.OwningProcess
        if ($ownerProcessId -le 0) { continue }
        $proc = Get-ProcInfo -ProcessId $ownerProcessId
        if ($null -eq $proc) {
            Stop-OrphanWorkersForOwner -OwnerPid $ownerProcessId
            continue
        }

        $cmd = ""
        if ($null -ne $proc.CommandLine) { $cmd = [string]$proc.CommandLine }
        $isFrontend = $listener.LocalPort -eq $FrontendPort -and (
            $cmd -like "*vite*--port $FrontendPort*" -or
            $cmd -like "*dev-server.mjs*" -or
            $cmd -like "*serve-dist.mjs*"
        )
        $isBackend = $listener.LocalPort -eq $BackendPort -and $cmd.Contains($rootText) -and $cmd -like "*uvicorn backend.main*"
        $isOldBackend = ($OldBackendPorts -contains $listener.LocalPort) -and $cmd.Contains($rootText)

        if ($isFrontend -or $isBackend -or $isOldBackend) {
            Stop-Pid -ProcessId $ownerProcessId -Reason "managed listener on port $($listener.LocalPort)"
        } elseif ($listener.LocalPort -eq $BackendPort) {
            throw "Port $BackendPort is owned by PID $ownerProcessId, but it is not a Churn process. CommandLine: $cmd"
        } elseif ($listener.LocalPort -eq $FrontendPort) {
            throw "Port $FrontendPort is owned by PID $ownerProcessId, but it is not the Churn Vite process. CommandLine: $cmd"
        }
    }
}

function Wait-PortClear([int[]]$Ports) {
    for ($i = 0; $i -lt 15; $i++) {
        $listeners = @(Get-Listeners -Ports $Ports)
        if ($listeners.Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    $still = @(Get-Listeners -Ports $Ports)
    if ($still.Count -gt 0) {
        $summary = ($still | ForEach-Object { "$($_.LocalPort)/PID $($_.OwningProcess)" }) -join ", "
        throw "Managed ports still have listeners after cleanup: $summary"
    }
}

function Wait-Json([string]$Url, [string]$Label) {
    for ($i = 0; $i -lt 25; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            $contentType = $response.Headers["Content-Type"]
            $body = [string]$response.Content
            if ($response.StatusCode -eq 200 -and $body.TrimStart().StartsWith("{") -and $contentType -like "*json*") {
                Write-Step "$Label OK"
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
            continue
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label did not return JSON from $Url"
}

function Assert-FinalListeners {
    $listeners = @(Get-Listeners -Ports $ManagedPorts)
    $backend = @($listeners | Where-Object { $_.LocalPort -eq $BackendPort })
    $frontend = @($listeners | Where-Object { $_.LocalPort -eq $FrontendPort })
    $old = @($listeners | Where-Object { $OldBackendPorts -contains $_.LocalPort })
    if ($backend.Count -ne 1) { throw "Expected exactly one backend listener on $BackendPort, found $($backend.Count)." }
    if ($frontend.Count -ne 1) { throw "Expected exactly one frontend listener on $FrontendPort, found $($frontend.Count)." }
    if ($old.Count -ne 0) { throw "Old backend listeners still active: $(($old | ForEach-Object { $_.LocalPort }) -join ', ')" }
    Write-Step "final listeners: backend $BackendPort PID $($backend[0].OwningProcess), frontend $FrontendPort PID $($frontend[0].OwningProcess)"
}

Write-Step "cleaning Churn processes and managed ports"
Normalize-ProcessPathEnv
Stop-ProjectProcesses
Start-Sleep -Seconds 1
Stop-ProjectProcesses
Wait-PortClear -Ports $ManagedPorts

if ($NoStart) {
    Write-Step "stopped; NoStart was set"
    exit 0
}

if (-not (Test-Path $Python)) {
    throw "Python interpreter not found: $Python"
}

$Node = (Get-Command node.exe -ErrorAction Stop).Source
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$BackendOut = Join-Path $Root "logs\backend-8000.out.log"
$BackendErr = Join-Path $Root "logs\backend-8000.err.log"
$FrontendOut = Join-Path $Root "logs\frontend-5176.out.log"
$FrontendErr = Join-Path $Root "logs\frontend-5176.err.log"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

Write-Step "starting backend on $BackendPort"
Start-Process -FilePath $Python `
    -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port $BackendPort" `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendOut `
    -RedirectStandardError $BackendErr

Wait-Json -Url "http://127.0.0.1:$BackendPort/api/run/status" -Label "backend $BackendPort"

Write-Step "building frontend"
Push-Location $FrontendDir
try {
    & $Npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "frontend build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Write-Step "starting frontend on $FrontendPort"
$env:CHURN_API_TARGET = "http://127.0.0.1:$BackendPort"
$env:CHURN_FRONTEND_PORT = "$FrontendPort"
Start-Process -FilePath $Node `
    -ArgumentList ".\serve-dist.mjs" `
    -WorkingDirectory $FrontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $FrontendOut `
    -RedirectStandardError $FrontendErr

Wait-Json -Url "http://127.0.0.1:$FrontendPort/api/run/status" -Label "frontend proxy $FrontendPort"
Wait-Json -Url "http://127.0.0.1:$FrontendPort/api/catalog/duplicates" -Label "fresh-route check"
Assert-FinalListeners
Write-Step "ready: http://127.0.0.1:$FrontendPort -> api http://127.0.0.1:$BackendPort"
