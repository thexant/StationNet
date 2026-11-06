# StationNet Theme Creator

A standalone, offline colour-theme editor. Open **index.html** in a modern
browser. No installation, build, internet connection, or StationNet server is
needed. Copy this entire directory anywhere to keep or share the app.

You can also upload the directory to a static web host, or preview it locally:

```sh
python -m http.server 8091 --directory theme-creator
```

Open <http://localhost:8091>. Run the command from the parent directory, or use
`python -m http.server 8091` from this directory.

## Create a theme

1. Start from the default palette, apply one of the ten built-in StationNet
   palettes, or **Import JSON** to edit an existing theme.
2. Use colour pickers or six-digit hex fields to change the station preview.
   On smaller screens, open **Edit your theme** at the bottom. The editor keeps
   fixed StationNet terminal colours so it remains usable with any preview palette.
3. In **Theme & export**, enter a name and author. The theme ID follows the name
   until you edit it; it must be unique on the station where you install it.
4. Include interface colours, masthead colours, or both, then **Export theme**.
   Excluded sections use defaults in the preview; their edits are retained.

The creator remembers one draft in browser storage when available. Browser
settings and direct-file storage policies can prevent saving; editing and
export still work. Direct-file storage may be specific to the file's location.
Keep exported JSON copies for reliable backups. Reset confirms before clearing
your draft and restoring defaults. Palettes and imported files replace the
current colours; export first if you want to keep another version.

## Install in StationNet

Open **SYSOP CONTROL → Station settings** and import your exported JSON.
Select its interface and/or masthead theme using the independent selectors.
Members can select imported themes when personal themes are enabled.

Alternatively, place `<id>.json` in `<STATIONNET_DATA>/themes/` and refresh
Station settings. Do not reuse an installed ID. `template-theme` is reserved
and cannot be exported by the creator.

## Preview fidelity

The preview recreates the service directory using StationNet's own labels,
descriptions, and empty-state messages, with no fictional users, posts, files,
or broadcasts. Station identity uses **THEME** and an unset callsign. Links
and radio controls never connect to a station. **THEME** uses the default
`small` FIGlet banner.

All 13 interface colours and both masthead colours use StationNet's portable
JSON format. Derived surfaces, RGB accents, default field/button tinting, and
Python's ties-to-even rounding match the current theme code. Custom field and
button values are used exactly. The radio controls provide real interface
fields and buttons. Colours used only in other pages or nonempty states may
not be visible in this preview. The editor does not simulate every station
module or display artificial notifications.

The standalone scripts contain a snapshot of the built-in palettes and their
descriptions. They do not load or modify StationNet at runtime. When the core
theme system changes, update this snapshot and preview styles deliberately.
No framework, external font, CDN, analytics, or service worker is used.

## Verify

From this directory, with Node.js installed:

```sh
node --test tests/core.test.cjs
```

When this directory is inside the StationNet checkout, cross-check the theme
format, all palettes, and colour calculations against the real Python code
using StationNet's Python environment (from the repository root):

```powershell
.venv/Scripts/python.exe theme-creator/tests/check_stationnet.py
```

The cross-check uses the existing StationNet environment only for verification;
it is not needed to run the creator. Tests and fixture helpers live here and
do not change StationNet files.

With an installed Chrome/Chromium browser, run the browser regressions from
this directory using `python tests/browser_check.py`. The runner copies the
app outside the checkout and checks direct-file and HTTP launch, UI controls,
downloads, draft restoration, invalid imports, storage failures, and responsive
layout. Screenshots and reports go into ignored `tests/artifacts/`. Use
`--browser PATH` to select another Chrome/Chromium executable. In restricted
Node environments, add `--test-isolation=none` to the unit-test command.

For browser checks: open `index.html` directly, test pickers and hex input,
metadata/ID generation, each export mode, reimport, refresh/draft restore,
failed imports, reset cancellation/confirmation, and blocked browser storage.
Repeat using HTTP and a copied folder outside the repository. Check desktop
sidebar scrolling, mobile drawer open/close/Escape, keyboard tabs/focus,
and overflow at 320px, 390px, 768px, 1100px and 1440px widths.
