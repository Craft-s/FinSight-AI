param(
    [string]$Distribution = 'Ubuntu-24.04',
    [string]$WslUser = 'root',
    [string]$PythonPath = '/var/tmp/finsight-venv/bin/python',
    [ValidateRange(5, 120)][int]$StartupTimeoutSeconds = 40
)

# Resume the existing Windows + WSL development setup; no install, migration,
# data import, secret output, or replacement of an existing port owner.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$logDirectory = Join-Path $projectRoot 'work'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$wslCommand = (Get-Command wsl.exe -ErrorAction Stop).Source
$shellCommand = (Get-Process -Id $PID).Path

function Invoke-LocalWsl([string[]]$Arguments) {
    $output = & $wslCommand -d $Distribution -u $WslUser --exec @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed. Check distribution '$Distribution' and PythonPath '$PythonPath'."
    }
    return ($output -join "`n").Trim()
}

function Quote-Literal([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function Start-LocalProcess([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$Directory, [string]$ProxyTarget = '') {
    # Encoded PowerShell with literal arguments preserves spaces and Unicode
    # without passing project paths through cmd.exe or an interpolated shell.
    $commandText = 'Set-Location -LiteralPath ' + (Quote-Literal $Directory) + "`n"
    if ($ProxyTarget) {
        $commandText += '$env:API_PROXY_TARGET = ' + (Quote-Literal $ProxyTarget) + "`n"
    }
    $commandText += '& ' + (Quote-Literal $Executable) + ' ' + (($Arguments | ForEach-Object { Quote-Literal $_ }) -join ' ') + "`nexit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($commandText))
    $process = Start-Process -FilePath $shellCommand -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -WindowStyle Hidden -WorkingDirectory $Directory -RedirectStandardOutput (Join-Path $logDirectory "local-$Name.stdout.log") -RedirectStandardError (Join-Path $logDirectory "local-$Name.stderr.log") -PassThru
    Write-Host "Started $Name (launcher PID $($process.Id))."
}

function Get-ServiceHealth([string]$BaseUrl) {
    try {
        $response = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 3
        if ($response.database -and $response.source_kind -and $response.task_mode) { return $response }
    } catch { }
    return $null
}

function Wait-ServiceHealth([string]$BaseUrl) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $health = Get-ServiceHealth $BaseUrl
        if ($health -and $health.database -eq 'ok' -and ($health.task_mode -ne 'rq' -or $health.redis -eq 'ok')) { return $health }
        Start-Sleep -Seconds 1
    } while ($timer.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds)
    throw "Service not ready at $BaseUrl. Check PostgreSQL/Redis and logs in '$logDirectory'. Existing processes were left running."
}

function Test-LocalPort([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.ConnectAsync('127.0.0.1', $Port)
        if ($pending.Wait(1000)) { return $client.Connected }
    } catch { }
    finally { $client.Dispose() }
    return $false
}

# Only one copy of this launcher may create processes at a time.
$lockPath = Join-Path $logDirectory 'local-start.lock'
try { $launcherLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
catch { throw 'Another local launcher is running. Wait for it to finish and retry.' }
try {
    $linuxRoot = Invoke-LocalWsl @('wslpath', '-a', '-u', $projectRoot)
    Invoke-LocalWsl @('test', '-x', $PythonPath) | Out-Null
    $addresses = Invoke-LocalWsl @('hostname', '-I')
    $wslIp = ($addresses -split '\s+' | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1)
    if (-not $wslIp) { throw 'No WSL IPv4 address found.' }
    $apiUrl = "http://${wslIp}:8000"
    $processProbe = @'
import json, os, pathlib, sys
backend = os.path.realpath(sys.argv[1] + '/backend')
state = {'api': False, 'worker': False}
for process in pathlib.Path('/proc').iterdir():
    if not process.name.isdigit():
        continue
    try:
        if os.path.realpath(os.readlink(process / 'cwd')) != backend:
            continue
        args = (process / 'cmdline').read_bytes().split(bytes([0]))
        state['api'] |= b'app.main:app' in args and b'uvicorn' in args
        state['worker'] |= b'app.workers.run' in args
    except (OSError, PermissionError):
        pass
print(json.dumps(state))
'@
    $state = Invoke-LocalWsl @($PythonPath, '-c', $processProbe, $linuxRoot) | ConvertFrom-Json
    $existingHealth = Get-ServiceHealth $apiUrl
    if (-not $state.api -and -not $existingHealth) {
        Start-LocalProcess 'api' $wslCommand @('-d', $Distribution, '-u', $WslUser, '--exec', $PythonPath, "$linuxRoot/scripts/manage.py", 'serve', '--host', '0.0.0.0') $projectRoot
    } else { Write-Host 'Reusing existing API process.' }
    $health = Wait-ServiceHealth $apiUrl
    if ($health.task_mode -eq 'rq' -and -not $state.worker) {
        Start-LocalProcess 'worker' $wslCommand @('-d', $Distribution, '-u', $WslUser, '--exec', $PythonPath, "$linuxRoot/scripts/manage.py", 'worker') $projectRoot
        $workerTimer = [Diagnostics.Stopwatch]::StartNew()
        do {
            Start-Sleep -Seconds 1
            $state = Invoke-LocalWsl @($PythonPath, '-c', $processProbe, $linuxRoot) | ConvertFrom-Json
        } while (-not $state.worker -and $workerTimer.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds)
        if (-not $state.worker) { throw "Worker did not stay running. Check '$logDirectory/local-worker.stderr.log'." }
    } elseif ($health.task_mode -eq 'rq') { Write-Host 'Reusing existing worker process.' }

    if (-not (Test-LocalPort 5173)) {
        $frontendDirectory = Join-Path $projectRoot 'frontend'
        if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'node_modules/vite'))) { throw 'Frontend dependencies missing. Run pnpm install --frozen-lockfile in frontend first.' }
        $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pnpmCommand) {
            Start-LocalProcess 'frontend' $pnpmCommand.Source @('dev', '--strictPort') $frontendDirectory $apiUrl
        } else {
            $nodeCommand = (Get-Command node -ErrorAction Stop).Source
            $pnpmRunner = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pnpm/bin/pnpm.cjs'
            if (-not (Test-Path -LiteralPath $pnpmRunner)) { throw 'pnpm not found. Install pnpm or add it to PATH.' }
            Start-LocalProcess 'frontend' $nodeCommand @($pnpmRunner, 'dev', '--strictPort') $frontendDirectory $apiUrl
        }
    } else { Write-Host 'Reusing the listener on port 5173; verifying its API proxy.' }
    Wait-ServiceHealth 'http://127.0.0.1:5173' | Out-Null
    Write-Host "Ready: http://127.0.0.1:5173 (source=$($health.source_kind), database=$($health.database), redis=$($health.redis))."
    Write-Host "Logs: $logDirectory/local-*.log"
} finally { $launcherLock.Dispose() }
