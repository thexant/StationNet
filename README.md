# StationNet

StationNet is a self-hosted community and communications system inspired by BBS software, monochrome terminals, early computer networks, and amateur radio. It brings messaging, broadcasting, shared media, reference collections, productivity tools, and games into one responsive browser workspace.

A station runs on a computer you control, and members connect from computers, tablets, or phones over the network. Each station has its own identity, accounts, content, and settings. No Docker, domain, cloud account, or separate database server is required.

The station operator, or **SYSOP**, chooses which modules to offer and supplies content such as books, videos, broadcasts, and game worlds. Available services therefore vary between stations; installing StationNet does not automatically populate every collection or enable every module.

## Features

### Community and conversation

A bulletin board, announcements, polls, imageboard, and events with RSVPs give a community places to share news and organize activities. Members have profiles and personal feeds with comments and reactions, a user directory, persistent public chat, temporary direct chats with online members, and private station mail.

### Radio and broadcasting

The shared radio supports configurable bands, half-duplex WebRTC voice, live Morse/CW keying, and simplified SSTV picture transmissions, with a docked receiver for listening while using other services. Authorized hosts can run audio playlists, relay external web radio, and build scheduled or automated programs combining music, announcements, jingles, Morse, pictures, and live microphone breaks.

### Television and videos

Station television offers scheduled channels, synchronized playback, and a program guide, so viewers tuning to a channel join the program currently on air. The separate Videos service provides an on-demand catalogue with search and playback, either curated by the station or open to member uploads and comments under SYSOP-defined quotas.

### Files and reference collections

The file archive provides shared uploads and downloads, while the Library offers EPUB and PDF reading with bookmarks and saved reading positions for members. The WIKI service uses Kiwix to browse and search locally stored ZIM archives, making reference collections available without an internet connection once the required tools and archives are installed.

### Office and productivity

The Office suite includes **Documents**, **Sheets**, **Presentations**, **Draw**, **Agenda**, and **BASIC** for writing, spreadsheets, slides, vector artwork, personal planning, and QBasic/QB64-style programming. Work is encrypted in the browser before storage using a separate Office password, with browser-based import and export; losing that password makes saved Office work unreadable.

### Programs and games

The Programs directory includes bundled games and utilities, creative tools such as MIDI Composer and an ASCII/ANSI art studio, and support for installing additional JavaScript or BASIC application packages. Programs can offer saved progress, touch controls, and multiplayer play, while the separate EmulatorJS Arcade runs supported retro games supplied by the operator or permitted contributors.

### MUD worlds

The optional native multi-user dungeon (MUD) offers persistent characters, exploration, quests, NPCs, combat, and procedural dungeon expeditions. Operators and authorized builders can create and edit worlds with the World Builder, import and export world packages, and run multiple authored worlds with their own content and character options.

### Administration and appearance

SYSOP controls cover module switches, registration and guest-access policies, permission groups, content management, and moderation through reports, account restrictions, and audit history. Station themes, FIGlet banners, icons, header messages, member appearance preferences, and installable web-app support let each station develop its own identity.

## Getting started

Source installations require **Python 3.11 or newer** on Windows, Linux, macOS, or Raspberry Pi OS. The installers create a local Python environment and download dependencies and the WIKI reader, so allow internet access during installation. Running the included browser tools requires no frontend build step.

### Windows

1. Extract or clone StationNet into a folder of your choice.
2. Double-click `install.bat` and follow the prompts.
3. Use the generated `run.bat` whenever you want to start the station.

### Linux and macOS

From the StationNet directory, make the scripts executable, install, and start:

```sh
chmod +x install.sh run.sh manager.sh uninstall.sh
./install.sh
./run.sh
```

### First-run setup

Open <http://127.0.0.1:8080> on the host computer. Choose the station name, description, registration policy, and credentials for the first SYSOP account. After initialization, use **SYSOP CONTROL** to select services, set permissions, and add content. Keep the SYSOP credentials safe.

For packaged Windows and Linux/Raspberry Pi releases, offline installation, and Android hosting, see [Installing a Packaged Release](docs/manual/stationnet-manual.md#333-installing-a-packaged-release) in the manual. Android runs a station on a supported phone; its available services differ from desktop hosting, including no WIKI reader.

## Hosting and station management

### Network access

The default listener is `0.0.0.0:8080`. Other devices on the LAN can connect at `http://<host-LAN-IP>:8080`; allow inbound TCP port 8080 through the host firewall if necessary. Choose loopback access to keep a station on the host computer only.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `STATIONNET_HOST` | `0.0.0.0` | Listening interface; use `127.0.0.1` for local-only access. |
| `STATIONNET_PORT` | `8080` | Listening port. |
| `STATIONNET_STATION` | `default` | Station ID to run; also selectable with `python run.py --station ID`. |
| `STATIONNET_HTTPS` | Unset | Set to `1` when clients reach the station through an HTTPS reverse proxy. |

Host, port, and HTTPS environment settings override the corresponding values in the station's `station.json`. Setting `STATIONNET_HTTPS=1` marks session cookies secure; it does not provide a TLS certificate or enable HTTPS by itself.

Browsers permit microphone access and web-app installation on localhost, but other devices need **HTTPS with a certificate they trust**. Live voice uses direct browser-to-browser connections without an external STUN/TURN service and is intended for reachable LAN clients; routed networks, restrictive firewalls, and internet use can prevent connections. See [Network Access](docs/manual/stationnet-manual.md#35-network-access) for secure hosting guidance.

An installed web app still needs a connection to the station for its content. Local collections can be used without internet access, but external radio sources need their upstream connection.

### Station Manager

Open `manager.bat` on Windows or `./manager.sh` on Linux/macOS to create and manage several stations from one installation. Each station runs independently, with its own port, accounts, and content. The manager can start, stop, and restart stations, open their pages and logs, and change server settings.

Daily station hours can automate opening and closing, with optional host sleep and wake support subject to operating-system configuration. The manager must remain open for station hours to operate. Its desktop interface requires Tkinter; on Debian and Raspberry Pi OS, install `python3-tk` if needed.

### Data and backups

The application code is shared, while each station keeps its database, identity, uploads, installed programs, themes, worlds, and WIKI archives in its own folder under `stations/`. A new default station starts in `stations/default/`; folders can subsequently follow the station's name, while its station ID stays stable. Standalone deployments can instead select a data directory with `STATIONNET_DATA`.

Stop a station before copying its complete folder for backup, and keep backups private. Manager-created ZIP backups exclude large WIKI (`.zim`) archives, so preserve those separately. See [Backup, Upgrade, and Maintenance](docs/manual/stationnet-manual.md#40-backup-upgrade-and-maintenance) for operating procedures.

Office encryption protects stored files and assumes you trust the host to serve the application code. Someone able to alter the server or its JavaScript is outside that protection boundary.

## Documentation

Use the [StationNet manual](docs/manual/stationnet-manual.md) for member instructions, hosting, administration, troubleshooting, and developer orientation. It is also available as a [PDF](docs/manual/StationNet-Manual.pdf), [HTML](docs/manual/StationNet-Manual.html), or [plain text](docs/manual/StationNet-Manual.txt).

Focused references cover content creation and extensions:

- [Broadcasting](BROADCASTING.md): radio programs, automation, and HTTP interfaces.
- [Application packages](stationnet/application_packages/APIREADME.md): package format and program runtime.
- [Colour themes](stationnet/theme_templates/THEMES_README.md) and [Theme Creator](theme-creator/README.md): authoring and importing custom palettes.
- [MUD worlds](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md): world packages and authored content, with related character and dungeon references.

## Development

StationNet uses **Python and Flask** for the application and HTTP APIs, **SQLite** for persistent records, and **Uvicorn** for HTTP and station-local WebSockets. Most pages use server-rendered HTML, CSS, and JavaScript; Office and MIDI tools include prebuilt browser bundles. Authentication uses salted password hashes, signed sessions, and CSRF protection.

`stationnet/` contains service modules and browser code, `run.py` starts a station, and `tests/` contains automated checks. To run the Python suite from an activated development environment:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```

When changing bundled browser tools, run `npm ci`, then the matching build script, such as `npm run build:documents` or `npm run build:midi`. See [package.json](package.json) for the available build and browser-test scripts, and the manual's [developer reference](docs/manual/stationnet-manual.md#part-vii-developers-reference) for project organization and extension guidance.
