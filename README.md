# StationNet

StationNet is a self-hosted, browser-accessible communications station inspired by BBS software, monochrome terminals, early computer networks, and amateur radio. It provides local station setup, accounts, a responsive terminal workspace, community services, an optional native MUD with persistent rooms and navigation, and an ephemeral shared radio with configurable bands, live Morse/CW keying, simplified SSTV picture transmissions, and half-duplex WebRTC voice. It requires no Docker, domain, cloud account, or external database.

MUD worlds can be shared as ZIP files in `data/mud_worlds/`. SYSOP Settings can import, enable, disable, and remove worlds, and several can run at once; the World Builder edits and exports whichever installed world you choose. Packages include rooms, exits, and optional items, NPCs, dialogue, quests, and placements. Use the [single-file MUD creator handbook](MUD_CREATOR_HANDBOOK.md) for a complete world handoff; the [file-authored world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md) remains a focused format reference.

Authored worlds can configure the following:

- quests that complete automatically, or that the player turns in to an NPC
- staged (sequential) and optional objectives
- quest deliveries and enemy defeats
- NPC-provided jobs and independent, repeatable profession work
- conditional dialogue that checks level, race, class, origin traits, inventory, coins, attributes, skills, and quest status
- races and classes with lasting gameplay effects: traits, proficiencies, resistances, skill affinities, innate abilities, and health, mana, and skill-point growth
- one-time dialogue rewards and costs
- coin rewards
- merchant NPCs with priced stock
- probabilistic combat loot
- renewable room items
- searchable discoveries
- keyed exits

Players interact with these using `/quests`, `/quest available`, `/quest accept`, `/quest turnin`, `/quest abandon`, `/deliver`, `/shop`, `/search`, and `/unlock`. Authored worlds also offer professions and repeatable work through `/professions`, `/profession join`, and `/work`. Builder exports include the corresponding settings.

Authored worlds can optionally offer banking through designated rooms or banker NPCs. Players use `/bank` to check carried and stored coins, `/bank deposit <amount>` to store coins, and `/bank withdraw <amount>` to retrieve them. Banked coins persist with the character and must be withdrawn before spending.

Authored worlds can also offer player housing and storage. World authors mark rooms as properties: unique homes with one holder at a time, or instanced apartments where every holder gets a private copy. Each property is bought, claimed for free, or granted through dialogue and triggers, with optional split rent. Players use `/house` to acquire, share with accepted co-owners, pay rent, or give up property, and `/storage` to keep items in personal lockers, shared stashes, or property storage. Only owners may enter a property, and whenever a holding ends, stored items return to whoever stored them. See section 5g of the [world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md).

Worlds can also offer vehicles and transit. Authors define vehicles with their own movement modes (water, rail, air, or any word), the wilderness terrain they can cross, speed, optional fuel, and an optional interior that carries passengers. Exits and wilderness presets can need a mode or be on foot only, so a boat crosses the bay a walker cannot, and a cart cannot fit down an alley. Public vehicles stand where authors place them; owned ones are granted through dialogue and triggers. Transit lines carry riders between stops for a fare after a set time, with optional cabins and operating hours. Players use `/board`, `/go`, `/travel`, `/disembark`, `/refuel`, `/vehicles`, and `/ride`, and `/directions` plans routes for whatever they are riding. See section 5h of the [world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md).

## Why this stack

- **Python 3.11+ and Flask** are small, readable, and cross-platform.
- **SQLite** is built into Python and keeps station-owned records in one local file. WAL mode permits concurrent readers and writers.
- **Uvicorn** serves HTTP and station-local WebSockets; Flask remains the application and HTTP API layer.
- Most pages use server-rendered HTML and a little CSS/JavaScript. The Documents editor ships with a prebuilt JavaScript bundle, so running StationNet needs no frontend build step.
- Passwords use salted `scrypt`; forms have CSRF protection; sessions use signed, HTTP-only cookies; optional remembered logins use a rolling ten-year cookie and remain valid across server restarts; responses include baseline security headers.

Future services can be Flask blueprints using the common terminal shell rather than isolated applications.

## Install and run

For offline Windows packages, Linux/Raspberry Pi wheel bundles, the standalone
Android APK project, and upgrade steps, see [DISTRIBUTION.md](DISTRIBUTION.md).
The scripts below are the existing source-checkout installation path and may
download dependencies.

Windows (easy setup):

1. Double-click `install.bat` and follow the prompts.
2. Use the generated `run.bat` to start StationNet at any time.
3. Open `http://127.0.0.1:8080` and complete first-run setup.

To host more than one station from the same installation, use the generated `manager.bat` instead (see *Station Manager* below).

To reset an installed copy, double-click `uninstall.bat`. It optionally backs up station data (the `stations/` folder, plus any pre-stations `data/` folder), including WIKI archives, then removes only the generated environment, station data, `run.bat`, `manager.bat`, and cache files. The StationNet directory and application source remain intact, ready for `install.bat` to be run again.

### Linux, macOS, and Unix

From the extracted StationNet directory, make the scripts executable once, then install and run:

```sh
chmod +x install.sh run.sh manager.sh uninstall.sh
./install.sh
./run.sh
```

`install.sh` requires Python 3.11 or newer and creates `.venv` in the StationNet folder. Run `./uninstall.sh` to optionally back up `stations/` (and any pre-stations `data/`), including WIKI archives, and remove only generated runtime files, leaving the source copy intact. Android hosting uses the standalone APK described in the distribution guide.

Windows PowerShell (manual setup):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8080` and complete first-run setup. By default the station listens on `0.0.0.0:8080`; another LAN device can use `http://<host-LAN-IP>:8080`. Permit inbound TCP port 8080 through the host firewall if needed.

## Runtime configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `STATIONNET_HOST` | `0.0.0.0` | Listening interface; use `127.0.0.1` for local-only. Overrides `host` in `station.json`. |
| `STATIONNET_PORT` | `8080` | Listening port. Overrides `port` in `station.json`. |
| `STATIONNET_STATION` | `default` | Id of the station to run from `stations/`; `run.py --station ID` does the same. |
| `STATIONNET_HOME` | folder containing `run.py` | Install root holding the core and `stations/`. Packaged launchers set it. |
| `STATIONNET_STATIONS_DIR` | `<home>/stations` | Alternative location of the stations directory. |
| `STATIONNET_DATA` | unset | Standalone mode: use exactly this directory as the station, bypassing the stations layout and migration (used by Android). |
| `STATIONNET_HTTPS` | unset | Set to `1` only when HTTPS is terminated in front of the app. Overrides `https` in `station.json`. |
| `STATIONNET_DISTRIBUTION_DIR` | `./dist` | Local ZIP/source package directory served on **Get Your Own Station**. |
| `STATIONNET_ANDROID_APK_DIR` | Android release APK output | Local directory scanned for signed StationNet Android releases. |

### Station layout

StationNet separates the shared core from station data:

```text
StationNet/
  run.py, stationnet/, .venv/, kiwix_tools/   shared core (packaged builds: core/)
  stations/
    default/                                  one self-contained folder per station
      station.json                            station settings: id, name, host, port, https, kiwix_serve
      run/                                    runtime files while it runs: lock, status, log
      station.db, secret.key, uploads/, applications/, themes/, mud_worlds/, kiwix_data/, ...
```

Every database, upload, installed program, theme, world, and WIKI archive a station writes stays inside its folder; the core is never written to. `station.json` is created on first start, and the environment variables above take precedence over its values.

**Upgrading from a `data/` folder.** When `stations/default/` does not exist yet and a pre-stations `data/` folder holds a station, the first start migrates it. Run `python run.py --migrate-only --dry-run` to preview, or `--migrate-only` to migrate without starting. Stop StationNet first; the migration refuses to run while the database or port is in use. It copies everything except ZIM archives into a staging folder, verifies each file's SHA-256 hash plus the database's integrity and row counts, and only then renames the staging folder to `stations/default/`. It then deletes the verified originals from `data/`, leaving the ZIM archives and a `MIGRATED.txt` note listing them; move those archives into `stations/default/kiwix_data/` by hand. If anything fails before the rename, `data/` is left untouched. Migration never overwrites an existing station folder; if both exist, StationNet refuses to start until one is moved aside.

Back up a station's folder to preserve station identity, accounts, messages, mail, announcements, the `uploads/` file archive, and sysop-installed games and apps in `applications/`. Bundled games and apps remain in StationNet's program files. Never publish a station folder's files.

### Station Manager

`manager.bat` (Windows), `./manager.sh` (Linux/macOS), or `manager.bat`/`manager.sh` in a packaged install opens a desktop window listing every station under `stations/`, with its name, status, port, and addresses. **+ New Station** suggests a name (`Station 1`, `Station 2`, …) and the next free port, asks who may connect, and creates the station in a temporary folder such as `stations/station-a7f92c/`. Each station can be started, stopped, restarted, opened in the browser, or have its log opened.

**Delete** on a station card removes the station for good. Type the station's name to confirm. A running station is stopped first. **Back up the station first** (on by default) saves a `.zip` of its folder wherever you choose, leaving out WIKI (`.zim`) archives, which can be very large. To restore a backup, unzip it into `stations/`. The station's folder is then deleted. If a file in it is open in another program, nothing is deleted and the error is shown.

New Station can also create the station's first SYSOP account (username, password, description, and registration policy, as on the first-run page). The manager stores only a password hash, in the station's `setup.json`; the station creates the account on its first start and deletes the file, so the browser never shows first-run setup. Leave the option unchecked to finish setup in the browser as before.

**Settings** on a station card changes the server settings kept in its `station.json`: name, port, access (this computer only, every network adapter, or one chosen IPv4 address of this computer), and **Served through an HTTPS proxy**, which makes session cookies secure-only for stations reached through a TLS reverse proxy. Ports already assigned to another station or in use by another program are refused. A running station keeps its current settings until it restarts: its card shows **Restart to apply…** with what changed, and the dialog offers **Save & Restart**. A new name reaches the station's database on its next start (`pending_name` in `station.json`); a later rename in Sysop settings takes precedence. Everything else about a station, such as its description, accounts, themes, and modules, stays in its own Sysop settings.

A station's id (the `id` in its `station.json`, also what `run.py --station` takes) never changes. Its folder follows its name instead: once a station is named, in the manager, at first-run setup, or later in Sysop settings, its folder is renamed to a file-safe form of the name (`Night Owl BBS` → `stations/night-owl-bbs/`). Stations with the same name get `-2`, `-3`, … folders, and names with no usable letters or digits keep their current folder. A running station's folder is never renamed: the manager shows the pending name and applies it when the station is stopped or restarted (starting with `run.py` applies it too). A rename is a single in-place move, so if it fails, for example because another program has a file open, the folder and its data stay exactly as they were, the error is shown, and the rename is tried again later.

Each station runs as its own process with only its own folder and `station.json`; environment overrides such as `STATIONNET_PORT` are not passed on. Stations started from the manager are detached from it, so closing the window leaves them running, and reopening it finds them again. Status comes from the station itself: while running it holds an OS lock on `run/station.lock` and describes itself in `run/status.json`, so a crashed or killed station is reported as **CRASHED** and its last log line is shown. **Stop** asks the station to shut down gracefully through `run/stop.request`. A station that has not stopped after 20 seconds offers **Force Stop**, which ends only that station's own process. Starting a station that is already running, including from `run.py` or `start.bat`, is refused. Tkinter is required; on Debian or Raspberry Pi OS install `python3-tk`.

#### Station Hours

**Hours** on a station card sets the station's daily opening and closing times. Hours are kept only in the station's `station.json` and are not part of the SYSOP settings page. While hours are on, the station shows them on a line under its header, for example `STATION HOURS: 8:00AM - 9:00PM` (or `08:00 - 21:00` with the 24-hour clock). Saved hours show at once; the station does not need a restart. A closing time earlier than the opening time runs past midnight.

- **Closing.** For the last few minutes before closing (5 by default, set per station as *Warning*), the station's card shows a countdown and a **Stay Online** button, and the manager window is brought forward. Stay Online cancels that closing: the station stays up until its next opening time, and its header line shows the *Extended* message instead of the hours (default `HOURS TEMPORARILY EXTENDED`). The next day's closing happens as usual. Without Stay Online, the station is stopped cleanly at its closing time.
- **Opening.** At its opening time a stopped station is started. If you stop a station by hand during its hours, it stays stopped until the next opening.
- **Opening early.** Starting a station during its closed hours shows the *Early Open* message in place of the hours (default `OPEN EARLY`). The normal hours return at the scheduled opening time, including on pages already open. A station kept running with Stay Online shows *Extended* until that opening instead. Stopping the station by hand ends its Stay Online extension.
- **SLEEP.** With SLEEP on, a station that closes is followed by a **Cancel Sleep** countdown (5 minutes by default). After it, the computer goes to sleep. Sleep is decided for the whole computer, not one station: if any other station is still running then, the computer stays awake. For example, Station A closing at 19:00 with SLEEP on does not sleep the computer while Station B, which closes at 23:59, is still running. Stations closing at the same moment are waited for.
- **Waking.** While any station has SLEEP on, the manager keeps one wake timer for this install. The timer is set a few minutes (5 by default) before the earliest next opening of any station with hours, and the stations then start at their opening times. If the computer is already awake when the timer fires, nothing extra happens. While a station is open, or about to open, the manager also keeps the computer from sleeping when idle.

**Sleep & Wake** in the manager's toolbar sets the sleep countdown and the wake lead time for this computer, and shows the next scheduled wake. These are saved in `manager.json` in the install folder. Station hours are kept only while a Station Manager is open, though it can be minimized. If several manager windows are open, only the first one carries out station hours. On Windows, the wake timer opens the manager, minimized, if it is not already open.

**Setting up wake on Windows.** The wake timer is a Task Scheduler task named `StationNet Wake <id>`. It runs as your account, so no administrator rights are needed, but you must be signed in (a locked screen is fine). Windows only honours it when wake timers are allowed:

1. Open **Control Panel → Power Options → Change plan settings → Change advanced power settings → Sleep → Allow wake timers** and choose **Enable** for both *On battery* and *Plugged in*.
2. In a command prompt, run `powercfg /a`. If *Standby (S3)* is available, wake timers work. If only *Standby (S0 Low Power Idle)* (Modern Standby) is listed, Windows may ignore wake timers. On such PCs, consider leaving SLEEP off so the computer stays on.
3. If the computer hibernates instead of sleeping, or does not wake, turn off *Hibernate after* and *hybrid sleep* in the same Sleep section. Some PCs also need *wake timers* or *RTC wake* enabled in their BIOS/UEFI settings.
4. To check your setup, open **Sleep & Wake** to see the next scheduled wake, then run `powercfg /waketimers` in a command prompt. The task is also listed in Task Scheduler. In a source install, `python -m stationnet.host_power status` prints the same details, and `python -m stationnet.host_power sleep` tests sleep.

**Linux and macOS.** Sleep uses `systemctl suspend` (Linux) or `pmset sleepnow` (macOS). Setting a wake timer needs root, so it is not automatic. Add a `wake_command` to `manager.json`, where `{epoch}` is the wake time in Unix seconds and `{iso}` is the local time. For example:

```json
{"wake_command": "sudo -n rtcwake -m no -t {epoch}"}
```

This needs a sudoers rule such as `youruser ALL=(root) NOPASSWD: /usr/sbin/rtcwake`. A `sleep_command` overrides how the computer is put to sleep on any platform.

### Optional imageboard

The imageboard is disabled by default. A SYSOP can enable it under **SYSOP CONTROL → IMAGEBOARD**. Signed-in station members can start image threads and post text replies with optional images. The catalog orders pinned threads first, then threads by their latest reply. SYSOPs can pin, lock, and delete threads or replies; the existing permission groups can grant **MODERATE IMAGEBOARD** to other members. Image uploads accept PNG, JPEG, GIF, and WebP up to 10 MB and use the first frame of animated images. The database and `imageboard/` image directory are both inside the station data directory; back up the whole directory to preserve them.

SYSOPs can set the maximum number of threads and image-bearing replies per thread. Posting a new thread prunes the oldest threads, including their replies and image files, when the thread limit is exceeded. Text-only replies remain available when a thread reaches its image reply limit.

### Optional Videos

Under **SYSOP CONTROL → VIDEOS**, SYSOPs can set a default member upload quota in MB and override it for individual users. Member uploads count toward their own library quota; station video uploads do not. The separate maximum video file size setting applies to both member and station uploads (120 MB by default, configurable up to 1,048,576 MB). Member uploads must also fit their remaining storage quota. Sizes use 1 MB = 1,048,576 bytes. Large uploads use temporary disk storage and restart if interrupted; allow space for the request, parsed upload, and saved video during processing.

### Optional Library

The **Library** module is disabled by default. SYSOPs can enable it under **SYSOP CONTROL → LIBRARY** and choose curated (station uploads only) or community (member contributions). EPUB books and PDF documents share the catalogue, a configurable per-file limit (512 MB by default), and configurable member quotas. Titles, authors, and descriptions are read from available file metadata. EPUB covers are extracted; PDF covers show a cached image of the first page, including for previously uploaded PDFs when their covers are first viewed. If a PDF page cannot be rendered, its title-based cover is shown. Readers can search, download, and read either format. EPUB chapters use StationNet's theme; PDF pages keep their original appearance in the browser's PDF renderer, while the surrounding controls follow the station theme. Signed-in readers get bookmarks and saved progress across devices on the same station. The reader and all files are hosted locally. Back up the station data directory to preserve `library/` and the database records.

The **Arcade** module is disabled by default. SYSOPs can enable it under **SYSOP CONTROL → ARCADE** and choose curated or community uploads. The catalogue lists every game by default and supports search, sorting, and simultaneous platform and genre filters. Game pages show a click-to-play EmulatorJS player and editable metadata and cover art. EmulatorJS 4.2.3 and default standard/legacy cores for NES, SNES, Game Boy, Game Boy Color, Game Boy Advance, Sega Genesis, Master System, Game Gear, Atari 2600/7800/Lynx, TurboGrafx-16, ColecoVision, Nintendo DS, and Nintendo 64 are bundled for offline play, along with the threaded PPSSPP core for Sony PSP (`.iso`, `.cso`, `.pbp`, or zipped). PSP games run on a separately bundled EmulatorJS 4.3.0-pre player, whose rebuilt PPSSPP core is much faster than the 4.2.3 one; this core is bundled only and cannot be downloaded or replaced from npm. PSP games need `SharedArrayBuffer`, so their pages are served cross-origin isolated and only play when StationNet is opened over HTTPS (see the reverse-proxy setup below) or on `localhost`, in a browser with WebGL2. MAME/Arcade ZIP sets are supported through downloadable MAME 2003-Plus, MAME 2003, FinalBurn Neo, and FinalBurn Alpha cores. Arcade ZIPs are passed intact to the core under their uploaded filename, so keep the driver's set name (for example, `pacman.zip`) and use a complete ROM set compatible with the selected core. Split clone sets depend on a separate parent set and cannot be launched alone. Sysops can download any missing compatible core from its official version-pinned npm package and choose one per platform. ROMs and downloaded cores live under `data/arcade/` and remain there when the module is disabled. Uploads have a 96 MB file limit (PSP uses a separate SYSOP-configurable limit, 1024 MB by default) and member uploads have configurable quotas. Very large PSP ISOs may exceed a browser's memory; compressed `.cso` images are smaller. Upload only game files you have permission to share.

### Optional WIKI (Kiwix)

The WIKI service is disabled by default. A SYSOP can enable it under **SYSOP CONTROL → WIKI**. The desktop installers include the official Kiwix reader; source installation downloads it automatically. Put trusted `.zim` archives directly in `data/kiwix_data/` (or `kiwix_data/` inside your `STATIONNET_DATA` directory). StationNet registers the archives and starts the local reader when a visitor opens WIKI. New or removed archives are picked up on the next WIKI request. JavaScript-first map archives and Project Gutenberg catalogs open in a restricted interactive frame so their bundled browsing controls and map renderers can operate. Gutenberg pages inherit the active StationNet palette. Interactive archives execute same-origin code and should therefore come only from a source the station operator trusts; ordinary article archives remain sanitized. The collection is included when you back up or restore the station data directory. On first use after an upgrade, StationNet moves archives from the older sibling `kiwix_data/` folder into the station data directory. Set `STATIONNET_KIWIX_SERVE` only to override the included executable.

### Optional Office Documents

The Office suite also includes **DRAW** for vector shapes, connectors, freehand paths, text, and inserted images on a configurable page. Drawings stay editable in the shared encrypted Office vault and export as SVG or PNG. Rebuild its included browser bundle with `npm run build:draw` after editing `stationnet/office_draw/`.

**BASIC** is a QBasic/QB64-style programming environment built on the bundled [QBJS](https://github.com/boxgaming/qbjs) compiler and runtime (`stationnet/static/vendor/qbjs/`, see `VERSION.md` there). Programs are saved in the shared encrypted Office vault, import and export as `.bas` files, and run with graphics, sound, and keyboard, mouse, or touch input. Each run happens in a fresh sandboxed frame with an opaque origin and no network access, so programs cannot reach StationNet, Office data, or the browser's storage. Rebuild with `npm run build:basic` after editing `stationnet/office_basic/`.

**EXPORT… → StationNet Package** turns a BASIC program into an installable Games & Apps ZIP: the source as `main.bas`, a generated manifest (name, identifier, description, author defaulting to the signed-in user, version, category, devices, input requirements, presentation), and optional asset files. Users only download the ZIP; a sysop installs it through **SYSOP CONTROL → MANAGE PROGRAMS** like any other package. Installed BASIC programs launch from the Programs directory in the same QBJS sandbox, with stop/restart, immersive and fullscreen modes, and per-user saves through `localStorage`. See [BASIC packages](stationnet/application_packages/APIREADME.md#basic-packages).

Enable **OFFICE** under SYSOP CONTROL to show the Office landing page. Its **DOCUMENTS** panel opens a separate rich-text editor and private document library. Documents autosave after editing pauses, show save status, and use revision checks to prevent a stale tab from overwriting newer work. The editor defaults to the user's StationNet palette; **PRINT THEME** previews black text on white. Printing always uses the white layout.

Each user sets one OFFICE Password for Documents, Sheets, Presentations, Agenda, and Draw. Unlocking one program unlocks the others in the same browser tab. The browser derives an encryption key from that password and a per-user random salt, then encrypts Office content with AES-GCM before sending it to the station. The server stores only ciphertext for the authenticated owner. SYSOP permissions do not bypass ownership. The password is not stored or recoverable; losing it makes saved Office data unreadable. A person who can change the station's server code or served JavaScript is outside this protection boundary.

Import and export run in the browser. Imports support DOCX, ODT, TXT, Markdown, HTML, and RTF; exports support DOCX, PDF, ODT, HTML, Markdown, and TXT. Imported HTML is sanitized and remote images are removed. DOCX and ODT keep common structure but can lose page layout, tracked changes, comments, fonts, and advanced styling. Markdown and TXT lose page styling; TXT also loses all formatting and images. RTF import can lose advanced formatting and embedded objects. PDF is a print snapshot. The prebuilt `stationnet/static/office-documents.js` is included for offline installation; developers changing `stationnet/office_documents/` can run `npm ci` followed by `npm run build:documents`.

Guests can read bulletins when guest access is enabled and can always read station announcements. Registered users can join the persistent public chat, open temporary direct chats with online users, exchange persistent private mail, use the shared file archive, maintain a profile, and operate the radio from every authenticated station page. Authorized hosts can upload private MP3, OGG, WAV, and MIDI audio, prepare playlists and reusable mixed-mode programs, and run server-timed stations on sysop-assigned frequencies; listeners joining midway synchronize to the current action. Programs combine playlists, announcements, jingles, Morse, SSTV, and live microphone breaks. Authorized DJs can take a single-controller lease, pause or skip, adjust music ducking, and use push-to-talk. SYSOPs can configure legacy playlist automation and persisted program schedules with time, interval, playback, and DJ lifecycle triggers. Radio signal events, playback positions, and direct traffic are held only in server memory. Continuous web radio plus active looping, repeating, and indefinite broadcasts are recorded as restart intents and automatically begin again after a server restart when their source and current radio permissions remain valid. The SYSOP can manage announcements, archive policy, radio band ranges and transmission capabilities, broadcast assignments and limits, and independent multi-group radio permissions under **SYSOP CONTROL**. Every account belongs to the permanent `DEFAULT` radio group; additional groups combine with explicit `DENIED` taking precedence over `FULL`, then `LISTEN`. `HOST BROADCASTS` is a separate group capability and is effective only alongside `FULL` access. `/` focuses service navigation when not typing; Escape releases focus.

Unread mail is shown in the MAIL navigation badge. Direct chat notifications appear in the CHAT navigation badge and as sender links on every signed-in page. They update through the existing two-second chat polling, use the interface sound preference, and clear when the conversation is viewed. Alerts are claimed once across tabs sharing a session. Direct messages and unread state remain in memory for the current online connections only; when either participant disconnects, the conversation and its alerts disappear. A notification opened after the sender leaves returns to the user directory with an availability notice.

The Programs directory groups software into Games, Utilities, Creative, Educational, Productivity, and Other. When Office is enabled, Sheets, Documents, Presentations, Agenda, and Draw appear in its Office category and use the shared OFFICE Password. Agenda combines month, week, and day planning with recurring events, tasks, contacts, search, and optional browser reminders while it is open and unlocked. The sysop can hide OFFICE links from the header and terminal while leaving these Programs entries available. The directory includes **MIDI Composer**, a multi-track piano roll with account-saved projects, touch controls, General MIDI instruments and drums, tempo changes, MIDI import, and `.mid` downloads for sharing or Radio uploads.

Sandboxed canvas apps can opt into a touch gamepad with a D-pad, optional analog joystick or steering control, customizable action buttons, and Start/Select. Multi-touch holds standard keys for `ui.onKey` and `ui.input.isDown`, and `ui.input.axis()` exposes analog values. Canvas pointer callbacks also report taps, drags, swipes, long presses, pinches, pointer IDs, and cancellation. The layout adapts to portrait, landscape, immersive mode, and mobile safe areas. See the [application package guide](stationnet/application_packages/README.md#canvas) for examples, key mappings, and limits.

## Station banner customization

Under **SYSOP CONTROL → Station settings**, the ASCII banner editor lists every FIGlet font installed with pyfiglet, with a compact searchable browser. It can use the station name or a separate banner text, size, theme-default/preset/custom hexadecimal colour, and a live preview rendered by the same server-side FIGlet path used by the masthead. Changes are only applied after saving; **Cancel Changes** restores the form and **Restore Banner Default** resets the banner controls before saving. Existing stations retain their current banner configuration; a no-longer-installed saved font safely falls back to `small` until a valid style is saved.

SYSOPs can also upload a station icon and use **Display station icon** to place it on both sides of the masthead throughout the site. The icon remains stored when display is turned off, and existing stations default to hiding it.

Under **SYSOP CONTROL → Station settings → Header taglines**, SYSOPs can add, edit, reorder, preview, and delete up to 32 header messages. Each message can inherit the current site theme's tagline color or use a custom hexadecimal color, and independently select a visual effect: static, blink, rainbow, glow, typewriter, marquee/ticker, scramble/decode, CRT flicker, glitch/interference, scanline sweep, ANSI color steps, reverse video, block reveal, wave/ripple, slide in/out, shimmer, phosphor fade, character jitter, letter spacing pulse, terminal scroll, or neon buzz. One message is chosen per page view; consecutive views avoid repeating the same text when another message is available. Existing single taglines are preserved as the first entry, Unicode symbols are supported, and animation stops for visitors who prefer reduced motion.

### Installable web app

Every station page uses one PWA manifest and a root-scoped service worker, so links between services stay in the same installed app. The app uses the station name. Under **SYSOP CONTROL → Station settings**, **Use Station icon for web-app** selects the uploaded station icon for the installed app independently of masthead display. If the setting is off or no station icon has been uploaded, the app uses the repository's `icon.png`. A connection is needed for station content; when disconnected, navigation shows a simple offline page. Browsers require HTTPS (or local `localhost`/`127.0.0.1`) to install a PWA, so LAN access through a plain HTTP IP address will not offer installation.

The same settings form offers independent **site-wide** and **masthead** palettes: Default/Cyan, Amber, Terminal Green, Bright Green, Magenta, Electric Blue, Crimson, Violet, Monochrome, and Vintage Terminal. All palettes retain the original dark backgrounds and control surfaces while changing text, borders, highlights, and accents. Members can independently override either palette, or inherit the station choice, under **Edit Profile → Theme preferences**. The saved banner hex colour remains available as a custom override; choosing a new masthead palette clears it. Palette definitions are centralized in `stationnet/themes.py`, and both choices are saved in the database. Existing stations continue to use the original palette until changed.

Custom colour themes can be shared as JSON files. A starter file is copied to `data/themes/template-theme.json` when the themes directory is first created; copy and edit it with a unique ID, name, author, and either the complete `site` section, the complete `masthead` section, or both. Its comment fields explain where each colour is used and can be left in place when importing. SYSOPs import the edited file in Station settings. Its available sections appear in the matching theme selectors with a `[CUSTOM]` label, including members' profile preferences when personal themes are enabled. SYSOPs can turn off personal themes to enforce the station site and masthead themes for everyone; saved member choices remain intact and apply again when personal themes are enabled. SYSOPs can remove imported themes there after checking a confirmation box; station selections return to Default / Cyan and member selections return to the station theme. Bundled palettes cannot be removed. See [the theme format](stationnet/theme_templates/THEMES_README.md).

The radio uses lightweight HTTP polling for control and WebRTC for live voice media, and is intended for one local StationNet process. A frequency admits one live transmitter at a time, independent of transmission type. On the radio page, tune to a voice-capable frequency with FULL access and select LIVE VOICE, then hold the on-screen PTT control (or hold **V** while focus is outside a text field) to talk; releasing it immediately stops the microphone track and clears the frequency. Callsigns, carrier state, and WebRTC negotiation messages are short-lived memory-only state. Voice audio travels directly between browsers and is never recorded, uploaded, or stored. CW key transitions and SSTV scanlines are likewise relayed as short-lived signal events and are never made available as history. The oscilloscope monitors the actual received or transmitted audio path. Browsers require a click or tap before allowing sound.

The native MUD can host several independent worlds at once. Each installed authored world, including the World Builder's own Station World, and the procedural dungeon world (the Dungeon Portals) is enabled or disabled separately under **SYSOP CONTROL > MUD service > Worlds**. Opening the MUD shows a world list; choosing a world opens the familiar game for that world only. Every world is its own game: an account creates a separate character in each world it plays, and level, attributes, skills, inventory, equipment, coins, bank balance, quests, reputation, and location stay in that world. Disabling a world hides it from players without deleting its content or characters, and builders can still edit it. Each world provides an authenticated text-first multiplayer interface with persistent authored rooms, characters, items and containers, reusable NPCs, branching dialogue, player-specific quests, and synchronized cooperative combat against hostile NPCs. New players create a character for a world through a six-step flow: independent character name, race, archetype, point allocation, skills, and review. Names must be unique within a world. The default generic fantasy options live in `stationnet/character_options.json`; a station operator can supply a separate character-options JSON through Flask's `MUD_CHARACTER_OPTIONS` config path. World ZIPs do not contain character options. Attribute budgets, race and archetype modifiers, starter equipment, and skill effects are validated on the server. Attributes and skills affect damage, defense, magic, stealth, escape, searching, lockpicking, merchant prices, health, and experience. Skills improve through use, and characters learn more with skill points or World Builder trainers (`/skills`, `/learn`, `/practice`, `/pick`). Characters from before the multi-world update stay in the world that was active, keep their levels, experience, quests, inventory, and achievements, and may use a one-time customization link. Players who had also explored the Dungeon Portals receive a dungeon-world character with a copy of that progression and their live run, and characters from archived campaigns become characters in their own worlds. A backup of the database is written to `data/backups/` before this one-time conversion. Console commands such as `/look`, `/go`, `/inventory`, `/talk`, `/respond`, `/quests`, `/combat`, and `/attack Rat 2` use server-authoritative action handlers. Use `/hide` to evade enemies; Agility improves hiding, enemy Defense improves detection, and attacking from hiding adds one-time surprise damage. Weapons, armor, character attributes, health, defeat/recovery, XP, and item drops participate in combat. Designated builders can manage rooms, exits, item/NPC definitions and placements, conversations, objectives, hostile combat profiles and equipment, respawn timing, and quest or combat rewards without editing code. The procedural dungeon world starts in a protected, SysOp-customizable Dungeon Portals hub kept outside builder-owned world data. Its deterministic generator validates external JSON themes and powers persistent solo or invite-code cooperative Random Expeditions plus a station-wide Dungeon of the Day. Daily layouts are snapshotted per configured station date and timezone, attempts have isolated mutable state, and only the first completion each day earns the completion reward. SYSOPs can enable or disable every built-in or imported dungeon theme without removing its definitions or affecting existing runs, and can separately remove imported themes after checking a confirmation box. Use `/expedition <theme> [difficulty] [size]`, `/join <code>`, `/daily`, `/daily enter`, `/daily join <code>`, `/run`, `/complete`, and `/abandon`. See the [dungeon theme guide](stationnet/dungeon_templates/README_DUNGEON_THEMES.md) and adjacent starter template. A fresh installation still contains only one generic starting room in Station World.

Authored worlds can also contain their own procedural dungeons, independent of the Dungeon Portals.
- **Building.** In the World Builder's **Dungeons** section, create templates in the dungeon theme format, either from scratch or by cloning an installed theme, and preview their layouts. Then add any number of **dungeon entrances** to rooms. Each entrance sets its template, a return room, difficulty, size, layout, party size, and entry conditions.
- **Playing.** Players type `/enter <entrance>` to get a newly generated private run. Others join that run with `/join <code>` at the same entrance.
- **Rules.** Runs use the world's progression, inventory, equipment, and resource name. They follow the usual dungeon rules for loot, death, and completion, survive disconnects (`/resume`), and return explorers to the entrance's return room.
- **Sharing.** World ZIPs carry these templates and entrances in an optional `dungeons.json`; see the [world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md).
- **Complete packages.** A world ZIP carries everything it needs to play on a clean station: its own races, classes, skills, abilities, effects, progression tuning (level cap, experience rate, health and mana per level, starting coins), and race/class availability. Imports are checked completely before anything is installed, with every problem listed by file and path. Re-importing an installed world upgrades it in place and keeps its characters. Dungeon themes can likewise bundle abilities and effects, tune rewards, risk loot, enemy scaling, and bosses, and be exported self-contained or upgraded by version. **SYSOP CONTROL > MUD service > Worlds** offers a complete example world to download.

Authored worlds can react to players through **triggers**, configured in the World Builder's **Events** section and shared in a world ZIP's optional `events.json`. A trigger names an event, such as entering a room, using or taking an object (`/use`, `/pull`, `/push`, `/interact`), saying a phrase, defeating an NPC, choosing a dialogue response, completing a quest, or a repeating timer. It then checks conditions on attributes, skills, race, class, items, quests, or saved player and world flags, and runs actions in order. Actions can send messages, set flags and counters, give or take items and coins, heal or damage, change object states, open, seal, or hide exits, show or hide NPCs and objects, spawn temporary NPCs, teleport, and start or advance quests, optionally after a delay. Triggers can fire once per character or once for the whole world, with cooldowns and fire limits. Chains are bounded, rewards go only to the acting player, and every world and dungeon run keeps separate state. Dungeon themes can carry run-local triggers too. Triggers are settings, not code; see section 5c of the [world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md).

Large contiguous wilderness areas are built from reusable **room presets** on the World Builder's **Wilderness** page. A preset defines a terrain, several room descriptions, environment details, possible creature encounters and gatherable resources. Sysops can create presets from bundled starters, copy, edit, import and export them, and fill rectangles, ellipses or brushed areas of a coordinate grid with them. Up to 250,000 rooms can be filled in one operation, and a world can hold up to 1,000,000. Every square is a real room with its own occupants, items and changes. Neighbouring rooms link automatically with ordinary directions (4-way or 8-way per preset, plus up and down between layers), while authored rooms, settlements, roads, landmarks and dungeon entrances keep their own exits and behaviour. Each room stores only its coordinates and preset; its description is chosen from its position, so it reads the same on every visit. Regions adjust danger, encounters and resources for the presets painted inside them. Players can use `/map` to see the land around them. To cross large areas, `/travel <direction>` walks a player onward one real room at a time (every 6 seconds by default; sysops can set the interval and a per-trip step limit under MUD service settings). Every room on the way is really visited, so encounters, resources, triggers and quests all fire. Travel stops for creatures and NPCs, other players, combat, terrain changes, authored rooms, paths out of the wilderness and blocked routes. It also stops on disconnect, on death, or when the player uses most other commands. `/stop` halts it at any time. World ZIPs carry wilderness in a compact run-length `wilderness.json`; see section 5d of the [world guide](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md).

Character option files define `point_buy`, `attribute_descriptions`, `races`, `classes`, `skills`, and `starting_skill_choices`. Races and classes can modify the four existing attributes, grant any skill effect type as a lasting bonus, grant starting skill ranks, and optionally declare `tags`, `proficiencies`, `resistances`, `skill_affinities`, innate `spells`, starter `equipment`, and `progression` growth (extra health, mana, per-level gains, skill points, or starting skill picks). Strength improves melee damage, melee accuracy, and equipped attack bonuses; Agility improves accuracy, evasion (dodging attacks), hiding, fleeing, and firearm damage; Intellect improves spells, spell accuracy, experience, and mana; Vitality improves health, mitigation, armor bonuses, and guarding. Health and mana gains on level up reflect Vitality and Intellect. Item definitions can set `properties_json.requirements` to an object such as `{"strength": 14, "level": 5, "tags": ["arcane"], "not_races": ["orc"]}` to restrict equipping by attributes, level, race, class, or origin tag. World exits can carry `conditions` that admit only matching characters. Item definitions can also set `properties_json.category` (such as `blade` or `heavy`); gear in a category the character has no proficiency skill for gives three quarters of its bonuses, while uncategorized gear is unaffected. Classes may name one favored skill and grant categorized equipment using existing item slots. The `skills` list holds the default skills; see the [skills guide](stationnet/mud_skill_templates/README_SKILLS.md) for effects, triggers, requirements, and progression. Choose option keys before players create characters; saved characters keep those keys and their attribute values if a world configuration changes later. Starter gear is placed in the new character's inventory and can be equipped with `/equip <item>`.

Character progression follows the existing experience curve (`100 × (level − 1)²` total XP) up to level 50. Each level grants maximum health and mana, as before, plus one attribute point (two at every fifth level). Spend points with `/train <attribute> [points]`; attributes cap at 30. Training Vitality or Intellect immediately applies the extra health or mana those attributes would have given at every level already earned. Levels also add combat bonuses on their own: +1 attack and spell damage every three levels, +1 healing and +1 accuracy every two levels, +1 evasion every four levels, and +1 mitigation every five levels. `/character` (or `/stats`) shows level, an experience bar, unspent points, trained attributes, and the resulting melee, firearm, spell, healing, accuracy, evasion, hit chances against an average foe, mitigation, flee, stealth, and experience figures. The sidebar shows health, mana, experience, and unspent points. Characters created before this system keep their level, experience, health, and mana and gain the level-based combat bonuses, but only levels gained afterward grant attribute points. Skill points are earned at every even level plus every fifth level, from the character's highest level, so existing characters receive them immediately. A skill rank costs `6 × rank` practice and can be at most four ranks above the character's level; rank-ups are announced as they happen. Trained points survive origin replacement; a legacy customization refunds them. Each dungeon difficulty is tuned for a recommended level of `3 × difficulty − 2`. `/expedition` lists recommended levels and warns when a run is far below or above yours. Dungeon kill and completion experience drops by 15% per level beyond three levels above the recommendation, never below 25%, and rises by 5% per level below it, up to +25%. Traditional-world rewards are unchanged.

**Combat rules.** Every attack first rolls to hit, then mitigation reduces the damage of hits that land. Evasion and damage reduction are separate mechanics.
- **Hit chance** is `85% + attacker accuracy − defender evasion`, kept between 30% and 95%. Evasion above 20 counts half, so very nimble characters are hard to hit but never untouchable.
  - Player accuracy: melee uses `(AGI−10) + (STR−10)/2`, firearms use `1.5 × (AGI−10)`, spells use `INT−10 + 10`. All three add the level bonus, skill `accuracy`, gear `accuracy`, and effects.
  - Player evasion: `2 × (AGI−10)`, plus the level bonus, skill `evasion` (the Evasion skill), gear `evasion` (which can be negative, for example on heavy armor), and effects. `/defend` adds 15.
  - NPCs use their combat profile's optional `accuracy` and `evasion` (default 0), plus gear and effects.
- **Outcomes.** A failed roll is reported as a *dodge* when the defender's evasion turned it aside, and as a *miss* otherwise. Misses deal no damage, apply no on-hit effects, and give no weapon practice; a missed shot still spends a round, and dodging trains Evasion.
- **Which attacks roll.** Melee, firearm, and damage-spell attacks roll; spells face only half of the target's evasion, and mana is spent even on a miss. Ambushes from hiding, self-targeted spells, lingering damage effects, and item use never roll.
- **Mitigation** for players is `3 + (VIT−10)/3`, plus armor, Guard and armor skills, the level bonus, and effects. Agility no longer reduces damage; it helps you avoid it.
- **Hostility.** Aggressive NPCs attack on sight. Neutral and friendly NPCs with a combat profile fight back against whoever attacks them, but leave other players alone. `passive` NPCs, such as training dummies, never strike back.
- **Enemy turns.** Every enemy fighting you acts after each of your combat actions, and lingering damage effects tick once per turn. When you enter a room, only the first aggressive enemy gets a free strike while the others join the fight. A successful `/flee` draws one parting strike.
- **Targeting with several players.** Enemies attack the player who acted, so idle players are never hit because of someone else's turn. A player who used `/defend` in the last 30 seconds guards the group: enemies they are fighting attack the guard instead, with defend's reductions applied.
- **Rewards.** Kill rewards follow contribution: damage dealt, plus half of the damage absorbed. Each contributor's share of the experience and coins is `40% + 60% × share` (a solo kill pays in full). A participant below 10% of the total contribution earns nothing; with more than five contributors, the threshold is lowered to `50% ÷ contributors`. Item drops roll separately for each eligible contributor.
- **Recovery.** `/rest`, outside combat, restores 25% of maximum health and 40% of mana in traditional worlds, or 15% and 25% inside a dungeon. It can be used every 30 seconds and lets one turn of lingering effects wear off.
- **Death.** Defeat never restores you fully. In a traditional world you awaken at the starting room with half health and mana and lose 10% of the experience earned toward your next level; you can never lose a level. In a dungeon, death ends the run and forfeits its loot, but costs no experience, and you return with half health and mana.

Races, classes, and skills live in one station-wide registry, and each world chooses which of them its players may use. SYSOP Settings > MUD service accepts individual race and class JSON files plus atomic JSON packs containing races, classes, or both, and offers each import in the worlds you tick (by default, every enabled world). Download the templates there or read [the race and class guide](stationnet/mud_origin_templates/README_RACES_CLASSES.md). Each world's **RACES, CLASSES & SKILLS** page lists every installed definition with where it came from and shortcuts to select all, none, the built-ins, or that world's bundled definitions; every world must offer at least one race, one class, and two starting skills. Disabling a choice in one world affects no other world and only stops new characters from picking it: existing characters keep it. Removing an imported definition deletes it from every world; characters that used it keep their progress and choose a replacement on their next visit, and a world left without any race, class, or starting skills falls back to the built-ins.

The **Skills** section beside it imports single skill files or atomic skill packs and bulk-removes imported skills after reviewing a summary. A skill a world does not offer keeps characters' ranks there but stops working, and refunds any skill points spent on it. Skills still required by an installed race, class, or skill cannot be removed, and skills characters hold are retained as inactive. World ZIPs (`skills.json`) and dungeon themes (`skills`) can bundle skills, and world content can include trainer NPCs, categorized gear, pickable locks, and hidden discoveries. See [the skills guide](stationnet/mud_skill_templates/README_SKILLS.md).

Classes, and races as innate abilities, grant abilities that spend mana and apply damage, healing, or timed effects: the seven built-in spells or custom abilities and effects that sysops install under **Abilities & Effects** or that world packages and dungeon themes bundle (see the [abilities guide](stationnet/mud_ability_templates/README_ABILITIES_EFFECTS.md)). `/spells` lists known spells, `/cast` uses them, and `/rest` restores part of your health and mana outside combat when safe. Each traditional world can rename mana (Energy, Stamina, Power, and so on) or disable it along with spells for settings without magic. The setting is in the World Builder and the world ZIP's `manifest.json`. The procedural dungeon world keeps Mana, while an authored world's own dungeons use that world's setting. Traditional worlds and dungeon themes can define firearms as hand equipment and matching ammunition as miscellaneous items; `/reload`, `/fire`, `/attack`, and `/melee` control them. The linked [race/class](stationnet/mud_origin_templates/README_RACES_CLASSES.md), [world](stationnet/mud_world_templates/README_CUSTOM_WORLDS.md), and [dungeon](stationnet/dungeon_templates/README_DUNGEON_THEMES.md) guides give the exact JSON fields, limits, and playable examples.

## Tests and layout

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

`stationnet/` contains the app factory, SQLite layer, authentication, service blueprints, templates, and static UI. `tests/` covers setup, authentication, callsigns, community services, live radio occupancy/CW/SSTV/tuning/permissions, CSRF, restart persistence, and the MUD (characters, progression and training, combat scaling, worlds, dungeons, and compatibility). `run.py` starts the HTTP and WebSocket server.

## Networking and future work

Plain HTTP is supported for `localhost`, which browsers treat as a secure context for microphone access. Browsers do **not** normally allow microphones on an HTTP LAN address such as `http://192.168.1.20:8080`; StationNet reports this instead of silently failing.

For voice access from other computers, put StationNet behind an HTTPS reverse proxy with a certificate trusted by every client. For example, run StationNet on loopback with `STATIONNET_HOST=127.0.0.1` and `STATIONNET_HTTPS=1`, then use a Caddy configuration like:

```caddyfile
stationnet.lan {
    tls internal
    reverse_proxy 127.0.0.1:8080
}
```

Make `stationnet.lan` resolve to the station computer and install Caddy's local root certificate as trusted on each client before opening `https://stationnet.lan`. A publicly trusted certificate can be used instead. Do not bypass certificate warnings: an untrusted certificate is not a supported microphone configuration. `STATIONNET_HTTPS=1` marks the login cookie secure, so enable it only when clients use the HTTPS URL.

Voice uses direct peer-to-peer ICE candidates and no external STUN/TURN service. It is designed for clients on the same reachable LAN. Routed/VLAN-isolated clients, restrictive endpoint firewalls, carrier-grade NAT, and internet use may require a separately operated TURN service, which StationNet does not currently configure. Propagation simulation and cross-station radio remain out of scope. See [BROADCASTING.md](BROADCASTING.md) for the program model, automation behavior, and HTTP API.

Later milestones may add expanded MUD gameplay, controlled federation, and more detailed radio propagation. Those systems remain outside this milestone.

# Moderation

SYSOP accounts can open **MODERATION** from the station navigation or SYSOP settings. The page lists accounts, current account and chat restrictions, open reports, and searchable audit history. An account page provides formal warnings, temporary suspensions, permanent bans, chat mutes, reinstatement, and recent public-chat bulk deletion. Reasons are required; private notes appear only in SYSOP history. Warnings also arrive as private station mail.

Users can report public chat messages, bulletins, files, and user behavior. Open reports are limited to one per user and target, with a daily limit of ten reports per account. SYSOPs review them in the moderation queue and record a resolution note. Existing bulletin and file removal controls remain available; SYSOP removals are audited. Public-chat message actions appear within each message.

Account restrictions are checked on every request, including API and radio paths. A temporary restriction expires automatically at its recorded UTC time; reinstatement ends active account restrictions, and unmute ends active chat mutes. Public-chat clients poll every two seconds for new messages and removals. Direct chat is temporary in-memory traffic, so it is not retained for retrospective moderation; user behavior can be reported from a profile. The bulk public-chat action removes up to the 500 most recent messages by the selected user per invocation.

# User Feeds

Each signed-in member has a feed on their profile page. The profile owner can
publish posts of up to 200 characters. Members can comment (up to 500
characters) and choose one reaction per post. Authors can delete their own
posts or comments; sysops can remove either, with an audit entry. Comments on
another member's post send that member a private-mail notification.

Feeds show 20 posts per page, newest first. A profile shows the three newest
comments on each post; opening a post shows 30 comments per page. Posting is
limited to five posts per 10 minutes, 20 comments per hour, and 60 reaction
changes per hour per account. Muted accounts cannot post, comment, or react.

Sysops can turn **User Feeds** on or off in **SYSOP Settings**. The switch
immediately hides the profile feed and rejects feed actions and feed reports.
Feed comment notifications are also hidden from private mail while disabled.
It retains existing posts, comments, reactions, and notifications for
re-enabling later.

