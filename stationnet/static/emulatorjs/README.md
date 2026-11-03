EmulatorJS 4.2.3 is bundled here from the official `@emulatorjs/emulatorjs`
and `@emulatorjs/core-*` npm packages. See `LICENSE` (GPL-3.0).

Bundled cores: fceumm, snes9x, gambatte, mgba, genesis_plus_gx, smsplus,
stella2014, prosystem, handy, mednafen_pce, gearcoleco, melonds, and
mupen64plus_next. Both standard and legacy WebGL data files are included.

The ppsspp (Sony PSP) core is not here: its 4.2.3 build is too slow for 3D
games, so PSP runs on the separate EmulatorJS 4.3.0-pre player in
`../emulatorjs-next/`.

StationNet uses the source player files because the npm package does not
contain the minified distribution. Its local copy of `src/emulator.js` omits
the upstream update probe and CDN core fallback so gameplay makes no network
requests. Its local `data/loader.js` also accepts `EJS_sourceFiles`, which loads
those source files without `EJS_DEBUG_XX`; debug mode makes cores log verbosely
and disables core/ROM caching, which badly slows emulation. Sysops can install other compatible 4.2.3 cores from official npm packages
using the Arcade settings panel. Those core files live in station data.
