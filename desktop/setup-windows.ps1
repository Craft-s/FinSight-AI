[CmdletBinding()]
param([string]$Distribution = $env:FINSIGHT_DESKTOP_DISTRO)

$ErrorActionPreference = 'Stop'
if (-not $Distribution) { $Distribution = 'Ubuntu-24.04' }
if ($Distribution -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$') {
    throw 'Invalid WSL distribution name.'
}

$bundle = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$wsl = Join-Path $env:WINDIR 'System32/wsl.exe'
if (-not (Test-Path -LiteralPath $wsl -PathType Leaf)) {
    throw 'Install WSL2 and Ubuntu 24.04 first: wsl --install -d Ubuntu-24.04'
}

# Decode Linux stdout as UTF-8 even in Windows PowerShell 5.1.
$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $resolved = & $wsl --distribution $Distribution --user root --exec wslpath -a -u $bundle
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu 24.04 is unavailable. Check wsl --list --verbose.' }
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
$linuxBundle = ($resolved -join "`n").Trim()
if (-not $linuxBundle.StartsWith('/') -or $linuxBundle.Contains("`n")) {
    throw 'Could not resolve the package location in WSL. Extract it to a local Windows folder.'
}

& $wsl --distribution $Distribution --user root --exec /usr/bin/python3 -I "$linuxBundle/desktop/runtime/setup.py" $linuxBundle
if ($LASTEXITCODE -ne 0) { throw 'FinSight AI initialization failed. Follow the setup message above, then retry.' }
