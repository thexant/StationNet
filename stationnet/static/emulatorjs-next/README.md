EmulatorJS 4.3.0-pre player files and its ppsspp (Sony PSP) core, from the
official release archive
https://github.com/EmulatorJS/EmulatorJS/releases/download/v4.3.0-pre/4.3.0-pre.7z
(sha256 0949d75fa5cff05c47e0431443dad6b65e2ebc5f1517cbb09f3d671236d3effd).
See `LICENSE` (GPL-3.0).

Only PSP uses this player. The 4.2.3 PPSSPP build in `../emulatorjs/` runs 3D
games far below full speed; 4.3.0-pre rebuilt it ("Fix hardware rendering for
PPSSPP core"). Cores are not interchangeable between EmulatorJS versions, and
4.3 cores are not published on npm, so this core is bundled only and the
Arcade settings panel cannot install or replace it.

The core needs WebGL2 and threads (`ppsspp-thread-wasm.data` plus
`ppsspp-assets.zip`; no legacy build). StationNet serves PSP game pages with
COOP/COEP headers; browsers expose `SharedArrayBuffer` only over HTTPS or on
localhost.

Local changes, matching the 4.2.3 copy:
- `data/loader.js` accepts `EJS_sourceFiles`, which loads the ES-module
  sources in `data/src/` without `EJS_DEBUG_XX` (debug mode logs verbosely and
  slows PPSSPP). The minified build is not bundled.
- `data/src/emulator.js` omits the upstream update probe and the CDN core
  fallback, and disables netplay, so gameplay makes no network requests.
- `data/src/GameManager.js` loads `ppsspp-assets.zip` from `EJS_pathtodata`;
  upstream hard-codes `data/cores/…` relative to the page, which 404s here.
- Only the English localization is kept (`EJS_disableAutoLang` is set).
