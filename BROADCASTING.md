# StationNet broadcasting architecture

StationNet has one radio arbiter and one server-owned hosted-playback clock. Unified
programs extend those components; they do not create a second transmission system.
Existing `radio_playlists`, `/radio/manage/start`, audio URLs, permission groups,
frequency assignments, and legacy `radio_automated_broadcasts` remain compatible.

External broadcasts use `radio_external_broadcasts` and reserve the same arbiter
frequencies as local broadcasts. They have no playlist clock, runtime limit, restart
delay, or indefinite-loop setting. An external broadcast that was on air is restored
automatically after a server restart. Tuned listeners receive MP3 and AAC audio through
`GET /radio/external/<transmission_id>/audio` on the StationNet origin. This avoids
browser mixed-content and cross-origin playback problems. The route requires a logged-in
receiver tuned to the active broadcast and closes its upstream connection when that
listener disconnects, retunes, or the broadcast stops. Each listener has one upstream
connection; the browser reconnects with bounded backoff after an interruption.
StationNet resolves M3U and PLS files with a size limit, validates public addresses
again after redirects and DNS resolution, and relays the continuous audio without
buffering the whole stream. HLS remains a direct URL and requires native browser
support. Sysop start warnings and server logs report connection errors. Stream URLs
should not contain private credentials.

## Persisted model

- `radio_broadcast_content` names reusable music, announcement, jingle, Morse, and
  SSTV assets. Audio entries reference the existing private `radio_audio_files` row.
- `radio_programs` contains reusable running orders.
- `radio_program_actions` sequences playlist/resume-playlist, audio, Morse, SSTV,
  and live microphone actions. `repeat_count` repeats a section without copying it.
- `radio_automation_rules` persists time, interval, track, completion, DJ-connect,
  and DJ-disconnect triggers. `next_run_at` lets the process resume schedules after
  restart.
- `radio_broadcast_resume` records active continuous, looping, repeating, and
  indefinite broadcasts. They restart from the beginning after a process restart,
  provided their source, frequency, band capabilities, and permissions remain valid.

At start time `compile_program()` resolves a program to the same timeline item shape
used by legacy playlists. The existing `HostedBroadcastStore` remains authoritative
for position, pause/resume, skip, duration correction, looping, and frequency
occupancy. Non-file actions carry `action_type`, `action_text`, and `content_id`; the
receiver reuses its Web Audio Morse oscillator, SSTV canvas, and WebRTC voice path.

## MIDI files

The private audio library accepts Standard MIDI Files (`.mid` and `.midi`) alongside
MP3, OGG, and WAV. MIDI works in ordinary playlists, unified programs, and legacy
automated broadcasts. Uploads are limited to 2 MB, 128 tracks, 200,000 events,
and 24 hours. The server validates type 0/1 structure and a PPQN tempo map, then
stores the computed duration on the existing `radio_audio_files` row. SMPTE time
division, format 2, embedded banks, and large SysEx data are unsupported. The
existing storage quota, host permissions, and tuned-listener file authorization
still apply.

The receiver loads `stationnet/static/midi/gm.sf2` on first MIDI playback and
uses the locally bundled SpessaSynth worklet. On HTTP LAN clients where
AudioWorklet is unavailable, it uses the same SoundFont through a
ScriptProcessor fallback. Both paths render GM programs 0–127 and channel-10
percussion in the browser; MIDI files themselves are sent through the
existing authorized audio file endpoint. Other radio paths still use their
existing audio element or WebRTC transport. The server's track serial and offset
remain authoritative for late join, pause, skip, shuffle, repeat, and automated
transitions. Receivers seek when drift exceeds 1.5 seconds. Local rendering can
vary slightly by device; the ScriptProcessor fallback can glitch if its main
thread is busy. Track changes are discovered by the existing receiver poll, so
network latency may leave a brief gap between different formats. MIDI is not
re-encoded into external HLS/ICY streams; external radio continues to use its
existing audio stream path.

The compact 14 MB FM-style GM SoundFont covers all 128 melodic programs and the
standard percussion notes. Its timbres are synthetic rather than acoustic. It
is distributed under GPL-3.0-only; its license is in
`stationnet/static/midi/SOUNDFONT-LICENSE.txt`. SpessaSynth's Apache-2.0 license
files are adjacent to the bundled engine and worklet. No CDN or runtime npm
installation is required. To rebuild the checked-in engine after changing its
source, run `npm ci` and `npm run build:midi`. Distribution builds copy the
prebuilt assets from `stationnet/static/midi`.

## HTTP endpoints

All mutating endpoints require the existing CSRF token. Host endpoints also recheck
the `HOST BROADCASTS` capability server-side; automation endpoints require SYSOP.

- `GET /radio/manage/programs` — content library and program editor.
- `POST /radio/manage/content` — create an audio reference, Morse message, or SSTV image.
- `POST /radio/manage/programs` — create a program.
- `POST /radio/manage/programs/<id>` — update program metadata and looping.
- `POST /radio/manage/programs/<id>/actions` — append a typed action.
- `POST .../actions/<id>/move|delete` — reorder or remove an action.
- `POST /radio/manage/programs/<id>/start` — start a program.
- `GET|POST /radio/manage/dj/status|control` — controller lease, live controls, and
  immediate reusable-content triggers. An interruption inserts a temporary action
  and resumes the interrupted audio at its server-authoritative offset.
- `GET|POST /radio/manage/automation[/*]` — manage server schedules.

The DJ lease is tied to both account and browser connection. Only one controller may
operate a broadcast. Leases expire after missed heartbeats, close the microphone,
and fire the persisted `dj_disconnect` fallback trigger. SYSOP may explicitly force
a handover through the control API (`force=1`).

## Operational notes

The scheduler is an in-process daemon timer and does not depend on an open browser.
StationNet is still designed as one application process; multiple web workers would
require an external scheduler/lease coordinator. WebRTC retains the existing
LAN-only ICE configuration, so remote DJ media has the same limitations as live voice.
