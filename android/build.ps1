$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT) { throw 'Set ANDROID_HOME or ANDROID_SDK_ROOT to an Android SDK with platform 35.' }
    $gradle = Get-Command gradle -ErrorAction SilentlyContinue
    if (-not $gradle) { throw 'Install Gradle 8.11.1 and JDK 17, then add gradle to PATH.' }
    & $gradle.Source --no-daemon :app:assembleDebug
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed: $LASTEXITCODE" }
    Write-Host "APK: $PSScriptRoot\app\build\outputs\apk\debug\app-debug.apk"
} finally { Pop-Location }
