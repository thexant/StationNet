# QBJS for Office BASIC

- Upstream: https://github.com/boxgaming/qbjs
- Version: `v0.11.5-beta` (commit `5b826a5a0c8ee441c587d354fb3aa99c048cc3ef`)
- License: MIT (`LICENSE`); bundled third-party notices in `3rd-party-licenses.html`.

## Files copied unchanged

`qb.js`, `qb2js.js`, `vfs.js`, `qbjs.woff2`, `fonts/`, `gx/__gx_font_default*.png`,
`util/pako.2.1.0.min.js`, `lib/**`, and from `codemirror/`: `codemirror.min.js`,
`codemirror.min.css`, `qb-lang.js`, `active-line.js`, `search.js`, `searchcursor.js`,
`dialog.js`, `dialog.css`.

## Local patches

- `gx/gx.js`, `touchstart`/`touchmove` handlers: touch coordinates are divided by
  the canvas's CSS transform scale. The Office BASIC runner fits the program
  screen with `transform: scale()`, which keeps mouse `offsetX/Y` correct but not
  QBJS's `pageX - rect.x` touch maths. Marked `StationNet patch`.

## How StationNet uses it

- `stationnet/office_basic/runner.js` (built to `static/office-basic-runner.js`)
  loads in `/office/basic/runner`, an `<iframe sandbox="allow-scripts">` with an
  opaque origin and a CSP that allows scripts only from this folder and the
  runner bundle, `'unsafe-eval'` for QBJS's compiled code, and no network,
  frame, or worker access.
- Fonts, the GX font sheets, and `lib/**` are embedded in the runner bundle and
  preloaded into the QBJS virtual file system, so `IMPORT "lib/..."` works
  offline. `fetch` is replaced with a VFS-only version; remote `IMPORT`,
  `IncludeJS`, `Fetch`, and `_LOADIMAGE` URLs are unavailable by design.
- Files a program offers for download are handed to the editor, where the user
  chooses whether to save them.

## Updating

Copy the files above from a newer tagged release, reapply the patch, update this
file and the `v=` cache keys in `templates/office/basic*.html`, then run
`npm run build:basic`, `npm run test:basic`, and
`python -m pytest tests/test_office.py tests/test_office_basic_browser.py`.
