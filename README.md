# StationNet

StationNet is a self-hosted, browser-accessible communications station inspired by BBS software, monochrome terminals, early computer networks, and amateur radio. It provides local station setup, accounts, a responsive terminal workspace, community services, an optional native MUD with persistent rooms and navigation, and an ephemeral shared radio with configurable bands, live Morse/CW keying, simplified SSTV picture transmissions, and half-duplex WebRTC voice. It requires no Docker, domain, cloud account, or external database.

## Why this stack

- **Python 3.11+ and Flask** are small, readable, and cross-platform.
- **SQLite** is built into Python and keeps station-owned records in one local file. WAL mode permits concurrent readers and writers.
- **Waitress** is a production-oriented, pure-Python server for Windows and Linux that handles simultaneous connections.
- Server-rendered HTML and a little CSS/JavaScript work offline and need no frontend build chain.
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

To reset an installed copy, double-click `uninstall.bat`. It optionally backs up station data, then removes only the generated environment, data, `run.bat`, and cache files. The StationNet directory and all application source files remain intact, ready for `install.bat` to be run again.

### Linux, macOS, and Unix

From the extracted StationNet directory, make the scripts executable once, then install and run:

```sh
chmod +x install.sh run.sh uninstall.sh
./install.sh
./run.sh
```

`install.sh` requires Python 3.11 or newer and creates `.venv` in the StationNet folder. Run `./uninstall.sh` to optionally back up `data` and remove only generated runtime files, leaving the source copy intact. Android hosting uses the standalone APK described in the distribution guide.

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
| `STATIONNET_HOST` | `0.0.0.0` | Listening interface; use `127.0.0.1` for local-only. |
| `STATIONNET_PORT` | `8080` | Listening port. |
| `STATIONNET_THREADS` | `16` | Waitress request-worker count. Increase this if sustained queue-depth warnings occur with many active clients. |
| `STATIONNET_DATA` | `./data` | Directory for `station.db` and `secret.key`. |
| `STATIONNET_HTTPS` | unset | Set to `1` only when HTTPS is terminated in front of the app. |
| `STATIONNET_DISTRIBUTION_DIR` | `./dist` | Local ZIP/source package directory served on **Get Your Own Station**. |
| `STATIONNET_ANDROID_APK_DIR` | Android debug APK output | Local directory scanned for the StationNet Android APK. |

Back up the data directory to preserve station identity, accounts, messages, mail, announcements, and the `uploads/` file archive. Never publish its files.

Guests can read bulletins when guest access is enabled and can always read station announcements. Registered users can join the persistent public chat, open temporary direct chats with online users, exchange persistent private mail, use the shared file archive, maintain a profile, and operate the radio from every authenticated station page. Authorized hosts can upload private audio, prepare playlists and reusable mixed-mode programs, and run server-timed stations on sysop-assigned frequencies; listeners joining midway synchronize to the current action. Programs combine playlists, announcements, jingles, Morse, SSTV, and live microphone breaks. Authorized DJs can take a single-controller lease, pause or skip, adjust music ducking, and use push-to-talk. SYSOPs can configure legacy playlist automation and persisted program schedules with time, interval, playback, and DJ lifecycle triggers. Radio signal events, active playback clocks, and direct traffic are held only in server memory and are discarded after expiry or server restart; program, content, and automation definitions remain saved. The SYSOP can manage announcements, archive policy, radio band ranges and transmission capabilities, broadcast assignments and limits, and independent multi-group radio permissions under **SYSOP CONTROL**. Every account belongs to the permanent `DEFAULT` radio group; additional groups combine with explicit `DENIED` taking precedence over `FULL`, then `LISTEN`. `HOST BROADCASTS` is a separate group capability and is effective only alongside `FULL` access. `/` focuses service navigation when not typing; Escape releases focus.

Unread mail is shown in the MAIL navigation badge. Direct chat notifications appear in the CHAT navigation badge and as sender links on every signed-in page. They update through the existing two-second chat polling, use the interface sound preference, and clear when the conversation is viewed. Alerts are claimed once across tabs sharing a session. Direct messages and unread state remain in memory for the current online connections only; when either participant disconnects, the conversation and its alerts disappear. A notification opened after the sender leaves returns to the user directory with an availability notice.

## Station banner customization

Under **SYSOP CONTROL → Station settings**, the ASCII banner editor lists every FIGlet font installed with pyfiglet, with a compact searchable browser. It can use the station name or a separate banner text, size, theme-default/preset/custom hexadecimal colour, and a live preview rendered by the same server-side FIGlet path used by the masthead. Changes are only applied after saving; **Cancel Changes** restores the form and **Restore Banner Default** resets the banner controls before saving. Existing stations retain their current banner configuration; a no-longer-installed saved font safely falls back to `small` until a valid style is saved.

The radio uses lightweight HTTP polling for control and WebRTC for live voice media, and is intended for one local StationNet process. A frequency admits one live transmitter at a time, independent of transmission type. Hold the on-screen PTT control (or hold **V** while focus is outside a text field) to talk; releasing it immediately stops the microphone track and clears the frequency. Callsigns, carrier state, and WebRTC negotiation messages are short-lived memory-only state. Voice audio travels directly between browsers and is never recorded, uploaded, or stored. CW key transitions and SSTV scanlines are likewise relayed as short-lived signal events and are never made available as history. The oscilloscope monitors the actual received or transmitted audio path. Browsers require a click or tap before allowing sound.

The native MUD's traditional world and procedural mode can be enabled independently under **SYSOP CONTROL**. It provides an authenticated text-first multiplayer interface with persistent authored rooms, characters, items and containers, reusable NPCs, branching dialogue, player-specific quests, and synchronized cooperative combat against hostile NPCs. New players create one character through a six-step flow: independent character name, race, archetype, point allocation, skills, and review. The default generic fantasy options live in `stationnet/character_options.json`; a world can supply its own JSON via Flask's `MUD_CHARACTER_OPTIONS` config path. Attribute budgets, race and archetype modifiers, starter equipment, and skill effects are validated on the server. Attributes and trained skills affect damage, defense, health, and experience. Skills improve through combat and experience awards. Existing characters can keep playing or use a one-time customization link; their levels, experience, quests, inventory, and achievements remain. Contextual controls and commands such as `/look`, `/go`, `/talk`, `/respond`, `/quests`, `/combat`, and `/attack` share the same server-authoritative action path. Weapons, armor, character attributes, health, defeat/recovery, XP, and item drops participate in combat. Designated builders can manage rooms, exits, item/NPC definitions and placements, conversations, objectives, hostile combat profiles and equipment, respawn timing, and quest or combat rewards without editing code. Procedural access uses a protected, SysOp-customizable Dungeon Portals hub kept outside builder-owned world data. Its deterministic generator validates external JSON themes and powers persistent solo or invite-code cooperative Random Expeditions plus a station-wide Dungeon of the Day. Daily layouts are snapshotted per configured station date and timezone, attempts have isolated mutable state, and only the first completion each day earns the completion reward. Use `/expedition <theme> [difficulty] [size]`, `/join <code>`, `/daily`, `/daily enter`, `/daily join <code>`, `/run`, `/complete`, and `/abandon`. See `stationnet/dungeon_templates/README.md` and the adjacent starter template. A fresh installation still contains only one generic traditional starting room.

Character option files define `point_buy`, `attribute_descriptions`, `races`, `classes`, `skills`, and `starting_skill_choices`. Races and classes can modify the four existing attributes and grant `attack`, `defense`, or `experience_percent` effects. Classes may name one favored skill and grant equipment using existing item slots. Skills use those same effect keys plus an optional `per_rank` multiplier. Choose option keys before players create characters; saved characters keep those keys and their attribute values if a world configuration changes later. Starter gear is placed in inventory for the character's current mode and can be equipped through the regular controls.

## Tests and layout

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

`stationnet/` contains the app factory, SQLite layer, authentication, service blueprints, templates, and static UI. `tests/` covers setup, authentication, callsigns, community services, live radio occupancy/CW/SSTV/tuning/permissions, CSRF, and restart persistence. `run.py` is the normal Waitress entry point.

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

