#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
    echo 'Set ANDROID_HOME or ANDROID_SDK_ROOT to an Android SDK with platform 35.' >&2
    exit 1
fi
command -v gradle >/dev/null || { echo 'Install Gradle 8.11.1 and JDK 17.' >&2; exit 1; }
gradle --no-daemon :app:assembleDebug
echo "APK: $(pwd)/app/build/outputs/apk/debug/app-debug.apk"
