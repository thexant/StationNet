# StationNet distribution

All builds use the shared `stationnet/` Flask application. The first visit to
`http://127.0.0.1:8080` opens the existing setup form, where the first sysop
chooses the station name, registration policy, account name, and password.
Keep port 8080 reachable on the host firewall for nearby devices. The station
serves local HTTP without any cloud account or internet connection.

## One-command build on Windows

Run from the development checkout in PowerShell after finishing source changes:

```powershell
.\build-all.ps1
```

The script builds every platform supported by the installed tools, prints a
status for each, and places successful packages plus `packages.json` in
`dist/`. The Get Your Own Station page reads this manifest for versions, sizes,
and SHA-256 checksums. It serves the files directly from `dist/` by default.
The script also creates `build/` at the checkout root with the finished ZIPs
from that run, named for each platform. The Android ZIP contains its signed APK.
Running the script again replaces those generated ZIPs, so `build/` does not
retain packages for platforms that were skipped or failed on the latest run.
Set `STATIONNET_DISTRIBUTION_DIR` before both building and starting the station
to use a different local download directory. You can limit a run with
`-Platforms Source,Windows` or select Python with `-Python py`. By default,
the script uses `.venv/Scripts/python.exe` when present, then the Windows `py`
launcher, then `python`.
For a release gate, pass `-RequireAll` with the platforms you intend to ship;
the command then exits unsuccessfully if any selected platform is unavailable
or fails. For example, `-Platforms Source,Windows -RequireAll` requires both
archives to build. Android requires the local release keystore described below.

Install Python 3.11+ and the application requirements first. Windows bundles
also need PyInstaller in the Python selected above (for this checkout,
`.\.venv\Scripts\python.exe -m pip install PyInstaller`). Linux builds need WSL
with a Linux distribution containing Python 3.11+, pip, venv, and any native
wheel build prerequisites. Use `-LinuxDistro Ubuntu` to select it; otherwise
WSL's default distribution is used. Docker Desktop's internal WSL distribution
is not a Linux build environment for this script. Raspberry Pi bundles need an **ARM** Linux build
environment (usually a Pi or ARM VM; an ordinary x64 WSL distro cannot make
compatible native wheels). If an ARM WSL distro is available, select it with
`-PiDistro NAME`. Android needs JDK 17, Gradle 8.11.1, desktop Python 3.11,
Android SDK platform 35 and build tools. The PowerShell Android builder detects
the usual per-user Android SDK, Gradle 8.11.1, and Eclipse Adoptium JDK 17
install locations; for other locations, set `ANDROID_HOME` or
`ANDROID_SDK_ROOT`, add Gradle to PATH, and set `JAVA_HOME` to JDK 17. Keep the
release keystore for future APK upgrades.

Each build uses the current checkout. Builds stage into a temporary directory;
only successful artifacts replace prior downloads. Unavailable and failed
platforms retain their last working packages. The script does not copy or
modify the station's `data/` directory. Distribution ZIPs and APKs stay outside
application package inputs. Run this manually when a new distribution is
needed; it does not publish to a remote service.

## Hosting downloads from a station

The unified build script stages packages in the station's `dist/` directory. The discreet
**Get Your Own Station** footer link inventories that directory and serves only
the package files actually present, so it works on an isolated LAN. Set
`STATIONNET_DISTRIBUTION_DIR` if the packages live elsewhere. Android builds
are also discovered from their normal release APK output directory; set
`STATIONNET_ANDROID_APK_DIR` to use another APK output directory.

To make a locally downloadable source archive, run:

```sh
python packaging/build.py source
```

This creates `dist/StationNet-source.zip` without any station data. The
archive includes `install.bat`, `install.sh`, both uninstallers, and the run
scripts, so it can be installed directly after extraction. Source installation
downloads the Kiwix reader along with Python dependencies; build the Windows
or Linux packages to provide an offline installation. The uninstallers
preserve `kiwix_data/`; back up its ZIM files separately when moving stations.
The downloads page also exposes its local README and this guide. If a `LICENSE` file is added to
the StationNet checkout, it is exposed there and included in source archives.

## Windows

On a Windows x64 build computer with Python 3.11+ and PyInstaller installed:

```powershell
python -m pip install -r requirements.txt PyInstaller
python packaging/build.py windows
```

The ZIP in `dist/` contains the embedded Python executable, dependencies, Kiwix reader, app
assets, `start.bat`, and `install.ps1`. On the destination computer, unzip it
and double-click `install.bat` (or run `start.bat` directly for a portable
copy). The installer writes `%LOCALAPPDATA%\StationNet\app` and
`%LOCALAPPDATA%\StationNet\start.bat`; start with the latter. No Python or pip
is needed on that computer. It writes all persistent data to the sibling
`data` directory. A portable copy writes data beside its `start.bat`.

To upgrade, stop StationNet, unzip a newly built ZIP and run its `install.bat`
again. It stages the new `app` folder, swaps it into place, and leaves `data`
alone. Keep a backup of `data` before upgrades. If a portable copy is used,
replace only `app` and `start.bat`, never `data`.

## Linux and Raspberry Pi

Build the offline archive on the same OS, CPU architecture, and Python ABI as
the destination. On the build computer, install Python 3.11+ with venv and pip,
then run:

```sh
python3 packaging/build.py posix
```

This resolves all dependency wheels at build time and stores them inside the
ZIP. It also includes the native Kiwix reader for the build platform. Pillow may need native build prerequisites on some Pi distributions;
the builder fails if it cannot make a wheel. Copy the ZIP to the destination,
unzip it, then run:

```sh
sh install.sh
~/.local/share/stationnet/start.sh
```

The destination needs a matching Python 3.11+ with venv; package installation
uses `--no-index` and needs no network. The installer puts replaceable code in
`~/.local/share/stationnet/app`, the virtual environment in `venv`, and all
persistent data in `data`. Set `STATIONNET_INSTALL_DIR` to another absolute
path if needed. The normal Python installation can itself be provided by the
OS image or local OS packages when installing on an air-gapped host.

To upgrade, stop the process, build a new archive on the target platform,
extract it, and run its `install.sh`. It installs dependencies from the bundled
wheels before swapping the app folder and preserves `data`. Back up `data`
first. Run a single StationNet process per data directory.

## Android APK

The Android project in `android/` uses Chaquopy to embed CPython 3.11 and the
same Flask/Jinja2/SQLite backend. The APK includes dependencies and needs no
Python, pip, terminal app, root access, mobile data, or internet to *install
or run*. It currently targets 64-bit ARM phones running Android 8 or newer.
The native WIKI reader is bundled with the Windows and POSIX distributions;
the Android APK does not yet include an Android build of `kiwix-serve`.
The Android build uses Pillow 11.0.0 because Chaquopy's Android repository has
that wheel for CPython 3.11, whereas the desktop 11.3.0 pin has no Android
wheel. The image APIs StationNet uses are present in 11.0.0.

To build, install JDK 17, Gradle 8.11.1, Android SDK platform 35 and build
tools, plus desktop Python 3.11 for Chaquopy's *build* process. Set
`ANDROID_HOME` or `ANDROID_SDK_ROOT`. Gradle downloads build plugins, embedded
Python, and Android dependency wheels while building; build on a connected
machine. Each build syncs the current `stationnet/` source and assets.

Create a release signing key once, on a trusted computer:

```powershell
keytool -genkeypair -v -keystore android/stationnet-release.jks -alias stationnet -keyalg RSA -keysize 3072 -validity 10000
```

Enter and securely record the passwords when prompted. Create the local file
`android/keystore.properties` with these four lines (replace the placeholders):

```properties
storeFile=stationnet-release.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=stationnet
keyPassword=YOUR_KEY_PASSWORD
```

Both files are gitignored. Back up the keystore and passwords securely; all
future release builds must use the same key. Keep the application ID
`net.stationnet.android` unchanged, and increase `stationnetVersionCode` in
`android/gradle.properties` for every update. Set `stationnetVersion` there
to the matching public version; it controls the installed version and APK name.

Build a signed release with `android/build.ps1` on Windows or
`sh android/build.sh` on Linux. The output is
`android/app/build/outputs/apk/release/StationNet-Android-v1.0.0.apk` for the
current version. `build-all.ps1 -Platforms Android -RequireAll` stages that
same APK in `dist/`, records its version and SHA-256 in `packages.json`, and
puts a ZIP containing the APK in `build/`. The Get Your Own Station page
offers the staged APK. For development, use `android/build.ps1 -Debug` or
`sh android/build.sh --debug`; debug APKs are not offered as public downloads.

Verify a release with the Android SDK tools, then test an upgrade on a device:

```powershell
apksigner verify --verbose --print-certs android/app/build/outputs/apk/release/StationNet-Android-v1.0.0.apk
adb install -r android/app/build/outputs/apk/release/StationNet-Android-v1.0.0.apk
```

Check that the certificate matches the backed-up key, the installed label and
icon display correctly, and the station's data remains after upgrading. A
previously installed debug APK uses a different certificate and cannot be
upgraded in place to this release key. Preserve or migrate its data before
uninstalling it; all subsequent releases signed with the same release key can
upgrade in place.

Open StationNet, tap **Start station**, and use the local browser button for
first-run sysop setup before enabling the hotspot. The screen shows active
IPv4 URLs for nearby devices.
Connect those devices to the same Wi-Fi or the phone's hotspot. The **Hotspot
settings** button opens the Android settings page when supported by the phone;
enable the hotspot there. Mobile data is unnecessary for local clients. The
notification keeps a foreground service running when the app is minimized and
has a Stop action. **Stop station** closes the server. Android can still kill
the process under battery or device policy; reopening the app and tapping
Start restores hosting. Some vendors disable the hotspot when mobile data is
off or isolate hotspot clients; test the chosen phone. The app does not toggle
hotspot automatically.

Android data lives in the app's private `files/station-data` directory and is
preserved by normal APK updates with the same signature. Uninstalling the APK
deletes that directory. This launcher has no in-app data export, so keep the
same signing key for upgrades. The app
listens on all interfaces on port 8080. Local HTTP works for text services;
browser microphone access on LAN HTTP remains restricted as described in the
main README.

## Verification boundaries

The source and bridge tests can run on desktop Python. The Windows executable,
Linux/Pi wheel archives, and Android APK must each be built and smoke-tested on
their target build environment. Hotspot routing and background lifetime require
a physical Android test.
