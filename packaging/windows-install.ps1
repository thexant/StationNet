$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = if ($env:STATIONNET_INSTALL_DIR) { $env:STATIONNET_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'StationNet' }
if (-not [System.IO.Path]::IsPathRooted($target)) { throw 'STATIONNET_INSTALL_DIR must be absolute.' }
if (-not (Test-Path -LiteralPath (Join-Path $source 'app\StationNet.exe'))) { throw 'Incomplete StationNet package.' }
New-Item -ItemType Directory -Force -Path $target | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $target 'data') | Out-Null
$stage = Join-Path $target ('app.new.' + [guid]::NewGuid().ToString('N'))
$old = Join-Path $target ('app.old.' + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath (Join-Path $source 'app') -Destination $stage -Recurse
$app = Join-Path $target 'app'
try {
    if (Test-Path -LiteralPath $app) { Move-Item -LiteralPath $app -Destination $old }
    Move-Item -LiteralPath $stage -Destination $app
    Copy-Item -LiteralPath (Join-Path $source 'start.bat') -Destination (Join-Path $target 'start.bat') -Force
} catch {
    if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $app)) { Move-Item -LiteralPath $old -Destination $app }
    throw
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
Write-Host "Installed at $target. Run $target\start.bat"
Write-Host 'Open http://127.0.0.1:8080 to create the first sysop account.'
