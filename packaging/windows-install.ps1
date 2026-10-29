$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = if ($env:STATIONNET_INSTALL_DIR) { $env:STATIONNET_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'StationNet' }
if (-not [System.IO.Path]::IsPathRooted($target)) { throw 'STATIONNET_INSTALL_DIR must be absolute.' }
if (-not (Test-Path -LiteralPath (Join-Path $source 'core\StationNet.exe'))) { throw 'Incomplete StationNet package.' }
New-Item -ItemType Directory -Force -Path $target | Out-Null
# Station data lives in stations\<id>. An existing data\ folder is left alone
# here; StationNet migrates it into stations\default on first start.
New-Item -ItemType Directory -Force -Path (Join-Path $target 'stations') | Out-Null
$stage = Join-Path $target ('core.new.' + [guid]::NewGuid().ToString('N'))
$old = Join-Path $target ('core.old.' + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath (Join-Path $source 'core') -Destination $stage -Recurse
$core = Join-Path $target 'core'
try {
    if (Test-Path -LiteralPath $core) { Move-Item -LiteralPath $core -Destination $old }
    Move-Item -LiteralPath $stage -Destination $core
    Copy-Item -LiteralPath (Join-Path $source 'start.bat') -Destination (Join-Path $target 'start.bat') -Force
} catch {
    if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $core)) { Move-Item -LiteralPath $old -Destination $core }
    throw
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
# Pre-stations installs kept the application (code only, never data) in app\.
$legacyApp = Join-Path $target 'app'
if ((Test-Path -LiteralPath (Join-Path $legacyApp 'StationNet.exe')) -and (Test-Path -LiteralPath (Join-Path $core 'StationNet.exe'))) {
    Remove-Item -LiteralPath $legacyApp -Recurse -Force
}
Write-Host "Installed at $target. Run $target\start.bat"
Write-Host 'Open http://127.0.0.1:8080 to create the first sysop account.'
