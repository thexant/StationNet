"""Bounded Standard MIDI File inspection for radio uploads.

The browser performs synthesis. This parser only validates untrusted uploads and
calculates the duration used by the server-owned broadcast clock.
"""

import struct

MAX_MIDI_BYTES = 2 * 1024 * 1024
MAX_TRACKS = 128
MAX_EVENTS = 200_000
MAX_DURATION = 24 * 60 * 60


class InvalidMidi(ValueError):
    pass


def _varint(data, position):
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise InvalidMidi("Truncated MIDI variable-length value.")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7f)
        if byte < 0x80:
            return value, position
    raise InvalidMidi("MIDI variable-length value is too long.")


def inspect_midi(payload):
    """Return a safe playback duration, rejecting malformed or costly SMF files."""
    if not 14 <= len(payload) <= MAX_MIDI_BYTES or payload[:4] != b"MThd":
        raise InvalidMidi("Expected a Standard MIDI File of at most 2 MB.")
    header_size = int.from_bytes(payload[4:8], "big")
    if header_size != 6 or len(payload) < 8 + header_size:
        raise InvalidMidi("Invalid MIDI header.")
    kind, count, division = struct.unpack_from(">HHH", payload, 8)
    if kind not in (0, 1) or not 1 <= count <= MAX_TRACKS or (kind == 0 and count != 1):
        raise InvalidMidi("Only type 0 and type 1 MIDI with up to 128 tracks are supported.")
    if division & 0x8000 or not 1 <= division <= 9600:
        raise InvalidMidi("MIDI must use a valid ticks-per-quarter-note time base.")
    position = 14
    tempo_events = [(0, 500_000)]
    last_tick = 0
    total_events = 0
    playable = False
    for _ in range(count):
        if payload[position:position + 4] != b"MTrk" or position + 8 > len(payload):
            raise InvalidMidi("Missing MIDI track chunk.")
        size = int.from_bytes(payload[position + 4:position + 8], "big")
        position += 8
        end = position + size
        if end > len(payload):
            raise InvalidMidi("Truncated MIDI track.")
        track = payload[position:end]
        position = end
        cursor = tick = 0
        running = None
        ended = False
        while cursor < len(track):
            delta, cursor = _varint(track, cursor)
            tick += delta
            total_events += 1
            if total_events > MAX_EVENTS or tick > 9600 * 60 * 60 * 24 * 4:
                raise InvalidMidi("MIDI sequence exceeds resource limits.")
            if cursor >= len(track):
                raise InvalidMidi("Truncated MIDI event.")
            status = track[cursor]
            if status & 0x80:
                cursor += 1
                if 0x80 <= status <= 0xef:
                    running = status
                else:
                    running = None
            elif running is not None:
                status = running
            else:
                raise InvalidMidi("Invalid MIDI running status.")
            if status == 0xff:
                if cursor >= len(track):
                    raise InvalidMidi("Truncated MIDI meta event.")
                kind = track[cursor]
                cursor += 1
                length, cursor = _varint(track, cursor)
                if cursor + length > len(track):
                    raise InvalidMidi("Truncated MIDI meta payload.")
                value = track[cursor:cursor + length]
                cursor += length
                if kind == 0x51:
                    if length != 3 or not 1000 <= int.from_bytes(value, "big") <= 60_000_000:
                        raise InvalidMidi("Invalid MIDI tempo.")
                    tempo_events.append((tick, int.from_bytes(value, "big")))
                if kind == 0x2f:
                    if length or cursor != len(track):
                        raise InvalidMidi("Invalid MIDI end-of-track event.")
                    ended = True
            elif status in (0xf0, 0xf7):
                length, cursor = _varint(track, cursor)
                # Embedded samples, manufacturer commands and large SysEx payloads
                # are outside the radio's General MIDI profile.
                if length > 256 or cursor + length > len(track):
                    raise InvalidMidi("Unsupported MIDI system-exclusive event.")
                cursor += length
            elif 0x80 <= status <= 0xef:
                length = 1 if status & 0xf0 in (0xc0, 0xd0) else 2
                if cursor + length > len(track) or any(byte & 0x80 for byte in track[cursor:cursor + length]):
                    raise InvalidMidi("Invalid MIDI channel event.")
                if status & 0xf0 == 0x90 and track[cursor + 1] != 0:
                    playable = True
                cursor += length
            else:
                raise InvalidMidi("Unsupported MIDI event.")
        if not ended:
            raise InvalidMidi("MIDI track is missing end-of-track.")
        last_tick = max(last_tick, tick)
    if position != len(payload) or not playable:
        raise InvalidMidi("MIDI file has trailing data or no playable notes.")
    duration_us = 0
    previous_tick = 0
    tempo = 500_000
    for tick, next_tempo in sorted(tempo_events[1:], key=lambda event: event[0]):
        if tick > last_tick:
            break
        duration_us += (tick - previous_tick) * tempo / division
        previous_tick, tempo = tick, next_tempo
    duration_us += (last_tick - previous_tick) * tempo / division
    duration = duration_us / 1_000_000
    if not .1 <= duration <= MAX_DURATION:
        raise InvalidMidi("MIDI duration must be between 0.1 seconds and 24 hours.")
    return duration
