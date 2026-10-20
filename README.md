# StationNet

StationNet is a self-hosted, browser-accessible communications station inspired by BBS software, monochrome terminals, early computer networks, and amateur radio. Milestone 2 provides local station setup, accounts, a responsive terminal workspace, persistent bulletins and station chat, temporary direct chat, private mail, shared files, user profiles, and operator announcements. It requires no Docker, domain, cloud account, or external database.

## Why this stack

- **Python 3.11+ and Flask** are small, readable, and cross-platform.
- **SQLite** is built into Python and keeps station-owned records in one local file. WAL mode permits concurrent readers and writers.
- **Waitress** is a production-oriented, pure-Python server for Windows and Linux that handles simultaneous connections.
- Server-rendered HTML and a little CSS/JavaScript work offline and need no frontend build chain.
- Passwords use salted `scrypt`; forms have CSRF protection; sessions use signed, HTTP-only cookies; responses include baseline security headers.

Future services can be Flask blueprints using the common terminal shell rather than isolated applications.

## Install and run

Windows (easy setup):

1. Double-click `install.bat` and follow the prompts.
2. Use the generated `run.bat` to start StationNet at any time.
3. Open `http://127.0.0.1:8080` and complete first-run setup.

To reset an installed copy, double-click `uninstall.bat`. It optionally backs up station data, then removes only the generated environment, data, `run.bat`, and cache files. The StationNet directory and all application source files remain intact, ready for `install.bat` to be run again.

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
| `STATIONNET_DATA` | `./data` | Directory for `station.db` and `secret.key`. |
| `STATIONNET_HTTPS` | unset | Set to `1` only when HTTPS is terminated in front of the app. |

Back up the data directory to preserve station identity, accounts, messages, mail, announcements, and the `uploads/` file archive. Never publish its files.

Guests can read bulletins when guest access is enabled and can always read station announcements. Registered users can join the persistent public chat, open temporary direct chats with online users, exchange persistent private mail, use the shared file archive, and maintain an optional text profile. Direct traffic is held only in server memory and is discarded on logout, connection expiry, or server restart. The SYSOP can manage announcements and configure archive uploads, total storage, and per-file limits under **SYSOP CONTROL**. `/` focuses service navigation when not typing; Escape releases focus.

## Tests and layout

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

`stationnet/` contains the app factory, SQLite layer, authentication, service blueprints, templates, and static UI. `tests/` covers setup, authentication, callsigns, bulletins, chat persistence and direct-chat expiry, private-mail isolation, profiles, files, announcements, permissions, CSRF, and restart persistence. `run.py` is the normal Waitress entry point.

## Networking and future work

HTTP is intentional for localhost, LAN, and direct-IP access. It does not protect credentials from observers, so only use plain HTTP on trusted networks. Browsers generally require a secure context (normally HTTPS) for microphone access, so the future radio/voice milestone will need optional HTTPS deployment. This milestone does not attempt certificates, NAT traversal, or internet discovery.

Later milestones may add persistent station-wide radio controls, virtual bands and voice, scheduled broadcasts, MUD hosting, and controlled federation. Those systems are intentionally outside this milestone. Radio should eventually remain part of the terminal environment while moving between services, not become a separate voice-chat application.

