# Bundled MIDI playback components

- `gm.sf2`: FM/GM Open SoundFont, `FM_GM_SoundFont_v0_2_1_mini.sf2`,
  by zeittresor. Source: https://github.com/zeittresor/opensoundfont .
  License: GPL-3.0-only, in `SOUNDFONT-LICENSE.txt`.
  SHA-256: `969a7db13c0713e2792ee9fc8a3e10505982c4875cc0c3483a2047ac7315561d`.
- `engine.js` and `spessasynth_processor.min.js`: built from
  `spessasynth_lib` 4.3.14 and `spessasynth_core` 4.3.22 by spessasus.
  Sources: https://github.com/spessasus/spessasynth_lib and
  https://github.com/spessasus/spessasynth_core .
  License: Apache-2.0, in `SPESSASYNTH-LICENSE.txt` and
  `SPESSASYNTH-CORE-LICENSE.txt`.

The unminified StationNet integration is `engine-source.js`. Run
`npm ci && npm run build:midi` from the source checkout to rebuild the engine.
