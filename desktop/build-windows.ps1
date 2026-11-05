[CmdletBinding()]
param([switch]$SelfContained)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$frontend = Join-Path $projectRoot 'frontend'
$hostProject = Join-Path $PSScriptRoot 'host/FinSight.Desktop.csproj'
$git = (Get-Command git -ErrorAction Stop).Source
$pnpm = (Get-Command pnpm -ErrorAction Stop).Source
$dotnet = (Get-Command dotnet -ErrorAction Stop).Source

# Only tracked application code, migrations and public archives enter the bundle.
# A Git checkout is required so untracked local archives cannot slip into a release.
$tracked = & $git -C $projectRoot ls-files -- backend/app backend/migrations data/snapshots data/documents
if ($LASTEXITCODE -ne 0 -or -not $tracked) {
    throw 'Build from a Git checkout with the backend and public archives available.'
}
$bundleFiles = @(
    'LICENSE'
    'THIRD_PARTY_NOTICES.md'
    'backend/alembic.ini'
    'backend/pyproject.toml'
    'backend/requirements.lock'
    'data/README.md'
    'desktop/runtime/host.py'
    'desktop/runtime/setup.py'
    'desktop/setup-windows.ps1'
    'docs/WINDOWS_DESKTOP.md'
)
$bundleFiles += $tracked | Where-Object {
    ($_ -like 'backend/app/*.py') -or
    ($_ -like 'backend/migrations/*.py') -or
    ($_ -eq 'backend/migrations/script.py.mako') -or
    ($_ -like 'data/snapshots/*.json') -or
    ($_ -like 'data/documents/*.json') -or
    ($_ -like 'data/documents/*.htm') -or
    ($_ -like 'data/documents/*.html') -or
    ($_ -in @('data/documents/README.md', 'data/documents/LICENSE-EDGARTOOLS.txt'))
}

& $pnpm --dir $frontend build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed. Install its locked dependencies and retry.' }

$outputRoot = Join-Path $projectRoot 'outputs'
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
if ((Get-Item -LiteralPath $outputRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The outputs directory must not be a symbolic link or junction.'
}
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$bundleName = 'FinSight-AI-Windows-x64-{0}-{1}' -f [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'), $suffix
$staging = Join-Path $outputRoot ".$bundleName.building"
New-Item -ItemType Directory -Path $staging | Out-Null

function Copy-BundleFile([string]$RelativePath, [string]$TargetPath = $RelativePath) {
    $source = Join-Path $projectRoot $RelativePath
    $item = Get-Item -LiteralPath $source
    if ($item.PSIsContainer) { throw "Expected a file: $RelativePath" }
    while ($item.FullName -ne $projectRoot) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symbolic links and junctions are not bundle inputs: $RelativePath"
        }
        $item = Get-Item -LiteralPath (Split-Path -Parent $item.FullName)
    }
    $destination = Join-Path $staging $TargetPath
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

$selfContainedValue = $SelfContained.IsPresent.ToString().ToLowerInvariant()
& $dotnet publish $hostProject --configuration Release --runtime win-x64 `
    --self-contained $selfContainedValue --output $staging `
    -p:DebugType=None -p:DebugSymbols=false
if ($LASTEXITCODE -ne 0) { throw 'Windows host publish failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $staging 'FinSight.Desktop.exe') -PathType Leaf)) {
    throw 'Windows host publish did not produce FinSight.Desktop.exe.'
}

# Use the same resolved SDK version that was published, even with a custom NuGet cache.
$projectAssetsPath = Join-Path (Split-Path -Parent $hostProject) 'obj/project.assets.json'
$projectAssets = Get-Content -LiteralPath $projectAssetsPath -Raw | ConvertFrom-Json
$webViewSdk = @($projectAssets.libraries.PSObject.Properties | Where-Object Name -Like 'Microsoft.Web.WebView2/*')
if ($webViewSdk.Count -ne 1) { throw 'Could not identify the restored WebView2 SDK.' }
$sdkDirectory = $projectAssets.packageFolders.PSObject.Properties.Name | ForEach-Object {
    Join-Path $_ $webViewSdk[0].Value.path
} | Where-Object { Test-Path -LiteralPath $_ -PathType Container } | Select-Object -First 1
if (-not $sdkDirectory) { throw 'Could not locate the restored WebView2 SDK files.' }
$notices = Join-Path $staging 'desktop/notices'
New-Item -ItemType Directory -Path $notices | Out-Null
Copy-Item -LiteralPath (Join-Path $sdkDirectory 'LICENSE.txt') -Destination (Join-Path $notices 'WebView2-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $sdkDirectory 'NOTICE.txt') -Destination (Join-Path $notices 'WebView2-NOTICE.txt')

foreach ($relativePath in $bundleFiles) { Copy-BundleFile $relativePath }
Copy-BundleFile 'desktop/Setup-FinSight.cmd' 'Setup-FinSight.cmd'
Copy-BundleFile 'frontend/dist/index.html'
Copy-BundleFile 'frontend/dist/favicon.svg'
$assets = Join-Path $frontend 'dist/assets'
foreach ($asset in (Get-ChildItem -LiteralPath $assets -Force)) {
    if ($asset.PSIsContainer -or $asset.Extension -notin @('.js', '.css', '.svg', '.woff2')) {
        throw "Unexpected frontend asset type: $($asset.Name)"
    }
    Copy-BundleFile "frontend/dist/assets/$($asset.Name)"
}

# Rename only this freshly created directory, after all required files exist.
Rename-Item -LiteralPath $staging -NewName $bundleName
Write-Output (Join-Path $outputRoot $bundleName)
