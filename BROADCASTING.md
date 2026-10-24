# StationNet broadcasting architecture

StationNet has one radio arbiter and one server-owned hosted-playback clock. Unified
programs extend those components; they do not create a second transmission system.
Existing `radio_playlists`, `/radio/manage/start`, audio URLs, permission groups,
frequency assignments, and legacy `radio_automated_broadcasts` remain compatible.

External broadcasts use `radio_external_broadcasts` and reserve the same arbiter
frequencies as local broadcasts. They have no playlist clock, runtime limit, restart
delay, or indefinite-loop setting. Receivers play resolved stream URLs directly;
StationNet only fetches bounded playlists and ICY metadata. M3U and PLS resolve to
their audio endpoint, while HLS requires native browser support. Browsers loading
StationNet over HTTPS may block HTTP streams as mixed content. Stream URLs are visible
to listeners and should not contain private credentials.

## Persisted model

- `radio_broadcast_content` names reusable music, announcement, jingle, Morse, and
  SSTV assets. Audio entries reference the existing private `radio_audio_files` row.
- `radio_programs` contains reusable running orders.
- `radio_program_actions` sequences playlist/resume-playlist, audio, Morse, SSTV,
  and live microphone actions. `repeat_count` repeats a section without copying it.
- `radio_automation_rules` persists time, interval, track, completion, DJ-connect,
  and DJ-disconnect triggers. `next_run_at` lets the process resume schedules after
  restart.

At start time `compile_program()` resolves a program to the same timeline item shape
used by legacy playlists. The existing `HostedBroadcastStore` remains authoritative
for position, pause/resume, skip, duration correction, looping, and frequency
occupancy. Non-file actions carry `action_type`, `action_text`, and `content_id`; the
receiver reuses its Web Audio Morse oscillator, SSTV canvas, and WebRTC voice path.

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
