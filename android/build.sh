#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
version=$(sed -n 's/^stationnetVersion=//p' gradle.properties | tr -d '\r')
test -n "$version" || { echo 'stationnetVersion is missing from android/gradle.properties.' >&2; exit 1; }
case "${1:-}" in
    '') task=assembleRelease; apk="app/build/outputs/apk/release/StationNet-Android-v$version.apk" ;;
    --debug) task=assembleDebug; apk=app/build/outputs/apk/debug/app-debug.apk ;;
    *) echo 'Usage: sh android/build.sh [--debug]' >&2; exit 2 ;;
esac
if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
    echo 'Set ANDROID_HOME or ANDROID_SDK_ROOT to an Android SDK with platform 35.' >&2
    exit 1
fi
command -v gradle >/dev/null || { echo 'Install Gradle 8.11.1 and JDK 17.' >&2; exit 1; }
if [ "$task" = assembleRelease ] && [ ! -f keystore.properties ]; then
    echo 'Create android/keystore.properties before building a release APK. See DISTRIBUTION.md.' >&2
    exit 1
fi
gradle --no-daemon ":app:$task"
test -f "$apk" || { echo "Expected APK is missing: $apk" >&2; exit 1; }
echo "APK: $(pwd)/$apk"
