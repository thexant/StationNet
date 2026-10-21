# StationNet

StationNet is a self-hosted, browser-accessible communications station inspired by BBS software, monochrome terminals, early computer networks, and amateur radio. It provides local station setup, accounts, a responsive terminal workspace, community services, and an ephemeral shared radio with configurable bands, live Morse/CW keying, simplified SSTV picture transmissions, and half-duplex WebRTC voice. It requires no Docker, domain, cloud account, or external database.

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

Guests can read bulletins when guest access is enabled and can always read station announcements. Registered users can join the persistent public chat, open temporary direct chats with online users, exchange persistent private mail, use the shared file archive, maintain a profile, and operate the radio from every authenticated station page. Authorized hosts can upload private audio, prepare playlists, and run server-timed music stations on sysop-assigned frequencies; listeners joining midway synchronize to the station's current track position. Radio signal events and direct traffic are held only in server memory and are discarded after expiry or server restart. The SYSOP can manage announcements, archive policy, radio band ranges and transmission capabilities, broadcast assignments and limits, and independent multi-group radio permissions under **SYSOP CONTROL**. Every account belongs to the permanent `DEFAULT` radio group; additional groups combine with explicit `DENIED` taking precedence over `FULL`, then `LISTEN`. `HOST BROADCASTS` is a separate group capability and is effective only alongside `FULL` access. `/` focuses service navigation when not typing; Escape releases focus.

The radio uses lightweight HTTP polling for control and WebRTC for live voice media, and is intended for one local StationNet process. A frequency admits one live transmitter at a time, independent of transmission type. Hold the on-screen PTT control (or hold **V** while focus is outside a text field) to talk; releasing it immediately stops the microphone track and clears the frequency. Callsigns, carrier state, and WebRTC negotiation messages are short-lived memory-only state. Voice audio travels directly between browsers and is never recorded, uploaded, or stored. CW key transitions and SSTV scanlines are likewise relayed as short-lived signal events and are never made available as history. The oscilloscope monitors the actual received or transmitted audio path. Browsers require a click or tap before allowing sound.

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

Voice uses direct peer-to-peer ICE candidates and no external STUN/TURN service. It is designed for clients on the same reachable LAN. Routed/VLAN-isolated clients, restrictive endpoint firewalls, carrier-grade NAT, and internet use may require a separately operated TURN service, which StationNet does not currently configure. Broadcast scheduling, recurring automation, propagation simulation, and cross-station radio remain out of scope.

Later milestones may add scheduled broadcasts, MUD hosting, controlled federation, and more detailed radio behavior. Those systems are intentionally outside this milestone.

