param([switch]$Debug)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$originalAndroidHome = $env:ANDROID_HOME
$originalJavaHome = $env:JAVA_HOME
try {
    if (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT) {
        $localSdk = Join-Path $env:LOCALAPPDATA 'Android/Sdk'
        if (Test-Path -LiteralPath (Join-Path $localSdk 'platforms/android-35/android.jar')) {
            $env:ANDROID_HOME = $localSdk
        } else { throw 'Android SDK platform 35 is unavailable. Set ANDROID_HOME or ANDROID_SDK_ROOT.' }
    }
    $gradle = Get-Command gradle -ErrorAction SilentlyContinue
    if ($gradle) { $gradlePath = $gradle.Source }
    else {
        $gradlePath = Join-Path $env:LOCALAPPDATA 'Gradle/gradle-8.11.1/bin/gradle.bat'
        if (-not (Test-Path -LiteralPath $gradlePath -PathType Leaf)) {
            throw 'Gradle 8.11.1 is unavailable. Install it or add gradle to PATH.'
        }
    }
    # Android Gradle Plugin 8.9 requires JDK 17. The system java may be newer.
    $jdk17 = Get-ChildItem (Join-Path $env:ProgramFiles 'Eclipse Adoptium') -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($jdk17 -and (Test-Path -LiteralPath (Join-Path $jdk17.FullName 'bin/java.exe'))) {
        $env:JAVA_HOME = $jdk17.FullName
    }
    if (-not $env:JAVA_HOME) { throw 'JDK 17 is unavailable. Set JAVA_HOME to a JDK 17 installation.' }
    if (-not $Debug -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'keystore.properties'))) {
        throw 'Create android/keystore.properties before building a release APK. See DISTRIBUTION.md.'
    }
    $task = if ($Debug) { ':app:assembleDebug' } else { ':app:assembleRelease' }
    & $gradlePath --no-daemon $task
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed: $LASTEXITCODE" }
    $versionLine = Get-Content (Join-Path $PSScriptRoot 'gradle.properties') | Where-Object { $_ -match '^stationnetVersion=' } | Select-Object -First 1
    if (-not $versionLine) { throw 'stationnetVersion is missing from android/gradle.properties.' }
    $androidVersion = ($versionLine -split '=', 2)[1].Trim()
    $apk = if ($Debug) { 'app\build\outputs\apk\debug\app-debug.apk' }
           else { "app\build\outputs\apk\release\StationNet-Android-v$androidVersion.apk" }
    $apkPath = Join-Path $PSScriptRoot $apk
    if (-not (Test-Path -LiteralPath $apkPath -PathType Leaf)) { throw "Expected APK is missing: $apkPath" }
    Write-Host "APK: $apkPath"
} finally {
    $env:ANDROID_HOME = $originalAndroidHome
    $env:JAVA_HOME = $originalJavaHome
    Pop-Location
}
