<#
.SYNOPSIS
Build and stage the StationNet downloads from this checkout.
.EXAMPLE
.\build-all.ps1
.EXAMPLE
.\build-all.ps1 -LinuxDistro Ubuntu -PiDistro PiBuilder -Platforms Linux,RaspberryPi
#>
[CmdletBinding()]
param(
    [ValidateSet('Windows','Linux','RaspberryPi','Android','Source')]
    [string[]]$Platforms = @('Windows','Linux','RaspberryPi','Android','Source'),
    [string]$Python = 'python',
    [string]$LinuxDistro = '',
    [string]$PiDistro = '',
    [string]$DistributionDir = '',
    [switch]$RequireAll
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $DistributionDir) {
    $DistributionDir = if ($env:STATIONNET_DISTRIBUTION_DIR) { $env:STATIONNET_DISTRIBUTION_DIR } else { Join-Path $root 'dist' }
}
$DistributionDir = [IO.Path]::GetFullPath($DistributionDir)
New-Item -ItemType Directory -Force -Path $DistributionDir | Out-Null
$work = Join-Path $DistributionDir ('.build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$originalTemp = $env:TEMP
$originalTmp = $env:TMP
$env:TEMP = $work
$env:TMP = $work
$results = [ordered]@{}
$published = @{}
$version = (Get-Date).ToUniversalTime().ToString('yyyyMMdd.HHmmss')
$revision = 'local'
if (Get-Command git -ErrorAction SilentlyContinue) {
    $rev = & git -C $root rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $rev) { $revision = $rev.Trim() }
}
$version = "$version+$revision"

function Invoke-Build([string]$Label, [scriptblock]$Action) {
    Write-Host "`n[$Label] Building..."
    try {
        $artifact = & $Action
        if (-not $artifact -or -not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw 'Build did not produce the expected package.'
        }
        $target = Join-Path $DistributionDir (Split-Path $artifact -Leaf)
        $pending = Join-Path $DistributionDir ('.pending-' + [guid]::NewGuid().ToString('N'))
        try {
            Copy-Item -LiteralPath $artifact -Destination $pending
            if (Test-Path -LiteralPath $target) {
                $backup = Join-Path $DistributionDir ('.backup-' + [guid]::NewGuid().ToString('N'))
                Move-Item -LiteralPath $target -Destination $backup
                try { Move-Item -LiteralPath $pending -Destination $target }
                catch { Move-Item -LiteralPath $backup -Destination $target; throw }
                if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
            } else { [IO.File]::Move($pending, $target) }
        } finally { if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Force } }
        $script:published[(Split-Path $target -Leaf)] = @{ platform = $Label; version = $version }
        $script:results[$Label] = "SUCCESS: $target"
        Write-Host $script:results[$Label]
    } catch {
        $status = if ($_.Exception.Message -match 'unavailable|cannot start') { 'UNAVAILABLE' } else { 'FAILED' }
        $script:results[$Label] = "${status}: $($_.Exception.Message)"
        Write-Warning "${Label}: $($_.Exception.Message)"
    }
}

function Invoke-PythonPackage([string]$Kind, [string]$Expected) {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) { throw "Python command '$Python' is unavailable." }
    & $Python (Join-Path $root 'packaging/build.py') $Kind --output-dir $work | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$Kind builder exited with code $LASTEXITCODE." }
    $artifact = Get-ChildItem -LiteralPath $work -Filter $Expected -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $artifact) { throw "No $Expected output found." }
    return $artifact.FullName
}

function Invoke-WslPackage([string]$Distro, [string]$Arch, [string]$Label) {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is unavailable.' }
    $args = @()
    if ($Distro) { $args += @('-d', $Distro) }
    $machine = & wsl.exe @args --exec uname -m
    if ($LASTEXITCODE -ne 0) { throw "WSL distro '$Distro' is unavailable or cannot start." }
    $machine = ($machine | Select-Object -Last 1).Trim()
    if ($Arch -eq 'pi' -and $machine -notin @('aarch64','armv7l','armv6l')) {
        throw "WSL distro '$Distro' is $machine; Raspberry Pi needs an ARM build environment."
    }
    if ($Arch -eq 'linux' -and $machine -in @('aarch64','armv7l','armv6l')) {
        throw "WSL distro '$Distro' is $machine; use -PiDistro for ARM."
    }
    $linuxRoot = & wsl.exe @args --exec wslpath -a -u $root
    if ($LASTEXITCODE -ne 0) { throw 'Could not map source path into WSL.' }
    $linuxWork = & wsl.exe @args --exec wslpath -a -u $work
    if ($LASTEXITCODE -ne 0) { throw 'Could not map build path into WSL.' }
    & wsl.exe @args --cd ($linuxRoot | Select-Object -Last 1).Trim() --exec python3 packaging/build.py posix --output-dir ($linuxWork | Select-Object -Last 1).Trim() | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$Label builder exited with code $LASTEXITCODE." }
    $artifact = Get-ChildItem -LiteralPath $work -Filter "StationNet-linux-$machine-*.zip" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $artifact) { throw "No Linux $machine package found." }
    return $artifact.FullName
}

try {
    foreach ($platform in $Platforms) {
        switch ($platform) {
            'Windows' {
                if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) { $results[$platform] = 'UNAVAILABLE: Python is missing.'; break }
                $oldPreference = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try { & $Python -c 'import PyInstaller' 2>$null; $installerExit = $LASTEXITCODE }
                finally { $ErrorActionPreference = $oldPreference }
                if ($installerExit -ne 0) { $results[$platform] = 'UNAVAILABLE: install PyInstaller in the selected Python.'; break }
                Invoke-Build $platform { Invoke-PythonPackage 'windows' 'StationNet-Windows-*.zip' }
            }
            'Linux' { Invoke-Build $platform { Invoke-WslPackage $LinuxDistro 'linux' 'Linux' } }
            'RaspberryPi' {
                if (-not $PiDistro) { $results[$platform] = 'UNAVAILABLE: specify -PiDistro with an ARM WSL environment.'; break }
                Invoke-Build $platform { Invoke-WslPackage $PiDistro 'pi' 'RaspberryPi' }
            }
            'Android' {
                if (-not (Get-Command gradle -ErrorAction SilentlyContinue) -or (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT)) {
                    $results[$platform] = 'UNAVAILABLE: Gradle or Android SDK is missing.'; break
                }
                Invoke-Build $platform {
                    & (Join-Path $root 'android/build.ps1') | Out-Host
                    if ($LASTEXITCODE -ne 0) { throw "Android builder exited with code $LASTEXITCODE." }
                    $apk = Join-Path $root 'android/app/build/outputs/apk/debug/app-debug.apk'
                    if (-not (Test-Path -LiteralPath $apk)) { throw 'Android APK is missing.' }
                    $named = Join-Path $work 'StationNet-Android-arm64-v8a.apk'
                    Copy-Item -LiteralPath $apk -Destination $named
                    return $named
                }
            }
            'Source' { Invoke-Build $platform { Invoke-PythonPackage 'source' 'StationNet-source.zip' } }
        }
    }

    # Retain previous working packages and their metadata when a build is skipped or fails.
    $manifestPath = Join-Path $DistributionDir 'packages.json'
    $old = @{}
    if (Test-Path -LiteralPath $manifestPath) {
        try { foreach ($entry in ((Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json).packages)) { $old[$entry.name] = $entry } }
        catch { Write-Warning 'Existing packages.json is invalid; rebuilding metadata from files.' }
    }
    $packages = @(
        foreach ($file in (Get-ChildItem -LiteralPath $DistributionDir -File | Where-Object { $_.Name -match '^StationNet.*\.(zip|apk)$' })) {
            $name = $file.Name
            $platform = if ($published.ContainsKey($name)) { $published[$name].platform }
                elseif ($name -match 'Windows') { 'Windows' }
                elseif ($name -match 'Android') { 'Android' }
                elseif ($name -match 'source') { 'Source' }
                elseif ($name -match 'aarch64|armv[67]|Raspberry') { 'RaspberryPi' }
                else { 'Linux' }
            $packageVersion = if ($published.ContainsKey($name)) { $published[$name].version }
                elseif ($old.ContainsKey($name)) { $old[$name].version }
                else { 'unknown' }
            [ordered]@{ name = $name; platform = $platform; version = $packageVersion; size = $file.Length; sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    )
    $json = [ordered]@{ schema = 1; generated_at = (Get-Date).ToUniversalTime().ToString('o'); packages = $packages } | ConvertTo-Json -Depth 5
    $tempManifest = Join-Path $DistributionDir ('.manifest-' + [guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($tempManifest, $json, [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $manifestPath) {
        $backup = Join-Path $DistributionDir ('.manifest-backup-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $manifestPath -Destination $backup
        try { Move-Item -LiteralPath $tempManifest -Destination $manifestPath }
        catch { Move-Item -LiteralPath $backup -Destination $manifestPath; throw }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    } else { [IO.File]::Move($tempManifest, $manifestPath) }
    Write-Host "`nManifest: $manifestPath"
} finally {
    $env:TEMP = $originalTemp
    $env:TMP = $originalTmp
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
Write-Host "`nBuild summary:"
foreach ($platform in $Platforms) { Write-Host "  ${platform}: $($results[$platform])" }
if (@($results.Values | Where-Object { $_ -like 'FAILED:*' }).Count) { exit 1 }
if ($RequireAll -and @($results.Values | Where-Object { $_ -notlike 'SUCCESS:*' }).Count) { exit 1 }
