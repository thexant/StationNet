# StationNet application packages

StationNet applications are ordinary ZIP files with exactly two root-level files:

```text
manifest.json
main.js
```

The file named by `entrypoint` may have a different `.js` name. Folders, native
executables, Python modules, dependencies, databases, HTML, CSS, and other files
are intentionally unsupported. Applications run in StationNet's existing
isolated JavaScript worker and use only the StationNet visual and storage APIs.

## Manifest

```json
{
  "package_format": 1,
  "slug": "my-application",
  "name": "My Application",
  "description": "A short explanation of what it does.",
  "author": "Your name or station",
  "version": "1.0.0",
  "category": "Apps",
  "entrypoint": "main.js"
}
```

`slug` is the application's permanent, unique identifier. Use lowercase letters,
numbers, and single hyphens; do not change it between releases. `category` must
be `Games` or `Apps`. StationNet currently supports package format `1` and does
not resolve dependencies or perform automatic updates.

## Create a package

Put the manifest and script in one folder, then create a ZIP with the *files at
the archive root*. On Windows PowerShell, from that folder:

```powershell
Compress-Archive -Path manifest.json,main.js -DestinationPath my-application.zip
```

Do not ZIP the containing folder itself. The included Number Guess demonstration
uses this exact format and is a useful starting point.

## Install and manage

Sign in as a sysop, open **SYSOP CONTROL**, then **MANAGE GAMES AND APPS**. Upload
the ZIP to install it. A valid package is enabled immediately and appears in the
normal Games and Apps directory. The same page can disable, re-enable, or
uninstall it. Disabled applications cannot be opened or use storage endpoints.

Uninstall removes the installed program files and registration, but deliberately
retains saved user/shared application data. Reinstalling the same slug can use
that data again. StationNet does not currently provide a data purge control.

Bundled applications use the same manifest, runtime, APIs, directory, and state
controls, and are labeled **BUILT-IN**. Their program files cannot be removed
through the third-party uninstall control. Uploaded apps are labeled
**THIRD-PARTY** and live in the station data directory rather than StationNet's
source tree.

## Runtime API quick reference

The smallest useful script is:

```js
StationNet.start((ui) => {
  ui.render(
    ui.title("Hello"),
    ui.text("Choose a response."),
    ui.menu("Response", ["Yes", "No"], ({ value }) => {
      ui.render(ui.text(`You chose ${value}.`));
    })
  );
});
```

Display methods include `render`, `append`, `update`, `remove`, `clear`, `title`,
`heading`, `text`, `list`, `table`, `preformatted`, and `grid`. Input methods
include `button`, `menu`, `input`, `numberInput`, `textarea`, `select`,
`checkbox`, and `onKey`.

### Rendering, ids, and lifecycle

`ui.render(...components)` replaces the complete application surface. `append`
adds components after the current surface. `clear()` is equivalent to rendering
no components. All three return `undefined`.

Give a component a stable `id` in its options when it will be changed later:

```js
ui.text("Waiting...", { id: "status" });
ui.update("status", ui.text("Ready.", { id: "status" }));
ui.remove("status");
```

`update(id, component)` replaces the identified component, except that updating
an existing canvas redraws that canvas in place. `remove(id)` removes the
identified component. IDs are strings (up to 80 characters; letters, numbers,
`_`, `.`, `:`, and `-` are preserved and other characters become `_`). They are
not assigned automatically, and an update/remove whose id is absent is a no-op.

For animation, render the fixed UI once, then update only the canvas on every
frame. Re-rendering the entire tree each frame is supported but replaces DOM
elements and is needlessly expensive; it can also discard current input/focus.

`ui.onVisibility(handler)` registers a handler called immediately and whenever
the page is hidden/shown or loses/gains focus. It receives an event object whose
`value` is `"hidden"` or `"visible"`; it returns an unsubscribe function. Pause
an animation while hidden and redraw when visible. `ui.onCleanup(handler)` adds
a function called when the application is terminated (for example, when its page
is left). Timer cancellations and registered cleanup handlers are run then, and
active synthesized sounds are stopped. Call unsubscribe functions yourself when
a listener is no longer wanted.

`ui.timeout(handler, milliseconds)` and `ui.interval(handler, milliseconds)`
return a cancellation function. Call it to cancel that one timeout or interval;
delays are clamped to 0–3,600,000 ms and intervals have a 10 ms minimum. Timer
callbacks may be async. The runtime catches callback failures and reports them
as application errors.

### Keyboard, buttons, and menus

`ui.onKey(handler)` receives an event object:

```js
{ key, code, ctrlKey, altKey, shiftKey, metaKey, value, values }
```

`key` and `code` use browser `KeyboardEvent` names, such as `"ArrowLeft"`,
`"a"`, `" "`, and `"KeyA"`; they are not normalized to names such as `"left"`.
Only `keydown` is reported—there is no keyup event—and a held key produces the
browser's normal repeated keydown events. Key handling is skipped while typing
in a text input, textarea, or select. The method returns an unsubscribe
function.

`button(label, onClick, options)` is a click/tap control. `onClick` receives the
same general event object (with `values`, the current named form values), but no
press/release or hold events. Canvas controls and menu choices are likewise
click/tap-only; implement continuous movement with keyboard repeats, canvas
pointer phases, or an interval.

`ui.menu(label, items, onSelect, options)` renders a labelled set of buttons.
Each item may be a string (used for both label and value) or
`{ label, value, disabled }`. `onSelect` receives `{ value, values, ... }`,
where `value` is always a string. `options` supports `id` and
`{ layout: "dpad" }`; other options are ignored. A d-pad uses the first four
items by *order*, not label: first = up, second = left, third = down, fourth =
right. It does not infer directions from the labels.

### Canvas

`ui.canvas(width, height, commands, options)` creates a raster playfield. Width
and height are logical CSS-pixel units, independently clamped to 80–1,200 and
80–800 respectively (defaults: 640 × 400 when invalid). At most 600 commands
are drawn per call. The background is opaque `#010401`.

The supported command objects are below. Coordinates and dimensions are logical
canvas pixels. Numeric values are finite, clamped to the listed range, and use
the stated default when missing or invalid.

| Command | Fields | Defaults and limits |
| --- | --- | --- |
| `rect` | `type: "rect"`, `x`, `y`, `width`, `height`, `color` | `x`: `0`, −width to 2 × width; `y`: `0`, −height to 2 × height; `width`: `1`, 0 to 2 × width; `height`: `1`, 0 to 2 × height; color defaults to `#00ed45`. |
| `circle` | `type: "circle"`, `x`, `y`, `radius`, `color` | `x`, `y` use the rect coordinate ranges; `radius`: `2`, 0 to the larger canvas dimension; color defaults to `#00ed45`. |
| `text` | `type: "text"`, `x`, `y`, `text`, `size`, `align`, `color` | `x`, `y` use the rect coordinate ranges; text is truncated to 200 characters; `size`: `18`, whole pixels, range 8–72; `align`: `"left"` (or `"center"`/`"right"`); color defaults to `#ffffff`. Text is vertically centered on `y`. |

Colors must be `#` followed by 3–8 hexadecimal digits (for example `#0f0`,
`#00ed45`, or `#00ed45cc`) or `rgb(r, g, b)` with decimal integer channels.
An invalid/missing color becomes the command's default. Unknown command types
are skipped; unknown fields are silently ignored. The host also silently clamps
or normalizes invalid supported values. There is currently no development-mode
warning facility for ignored fields, so validate command construction in your
own tests.

Canvas options are `{ id, label, onPointer, hover, controls }`. `id` enables
`update`; `label` is the accessible name and defaults to `"Game playfield"`.
`onPointer(event)` receives the scaled logical `{ x, y, phase, values, ... }`;
`phase` is `"start"`, `"move"`, or `"end"`. `hover` defaults to `false`; when
true, mouse moves are reported without a pressed button (touch and dragging are
reported either way). `controls` defaults to no controls and accepts up to four
`{ label, onClick, variant }` entries. `label` defaults to `"Action"`,
`onClick` receives the normal interaction event, and `variant` may be
`"secondary"` (anything else is the default style). Controls are an accessible
centered overlay covering the playfield; there are no placement options.

Here is a complete, short animated canvas example. It draws only the canvas each
frame, uses browser arrow-key names, and stops work while the page is hidden:

```js
StationNet.start((ui) => {
  let x = 130, vx = 0, visible = true;
  const draw = () => ui.update("field", ui.canvas(320, 180, [
    { type: "text", x: 12, y: 18, text: "ARROWS TO MOVE", size: 12, color: "#55ff55" },
    { type: "circle", x, y: 100, radius: 12, color: "#00ed45" }
  ], { id: "field", label: "Moving dot" }));

  ui.render(ui.title("Dot runner"), ui.canvas(320, 180, [], { id: "field", label: "Moving dot" }));
  ui.onKey(({ key }) => { if (key === "ArrowLeft") vx = -3; if (key === "ArrowRight") vx = 3; });
  ui.onVisibility(({ value }) => { visible = value === "visible"; if (visible) draw(); });
  ui.interval(() => { if (!visible) return; x = Math.max(12, Math.min(308, x + vx)); draw(); }, 33);
  draw();
});
```

Interactive character canvases can use `cellGrid(rows, options)`. Each cell is
an object such as `{ char: "#", fg: 7, bg: 0 }`, where foreground and background
are controlled 0–15 ANSI palette indexes. `options.onCell` receives bounded
`x`, `y`, and `drag` values; the host always renders characters as text.
The event also includes a `phase` (`start`, `move`, or `end`), and an optional
`options.brush` provides immediate, safely rendered feedback during dragging.

Two bounded browser conveniences are also available: `ui.clipboard.writeText(text)`
and `ui.download.text(filename, text)`. They return promises and accept text only;
applications do not receive general browser, DOM, network, or filesystem access.
Colored cell grids can be exported as a controlled PNG with
`ui.download.cellImage(filename, rows, options)`.

## Synthesized sound

Applications can generate sound with basic oscillators; they cannot upload, load,
or play audio files. `ui.sound.playTone(frequency, duration, options)` plays a
single tone, with frequency in Hz and timing in milliseconds:

```js
ui.sound.playTone(880, 90, { waveform: "square", volume: 0.12 });
```

Use `ui.sound.play(tones, options)` for tones that may overlap (a chord or SFX
layer), and `ui.sound.sequence(tones, options)` for consecutive notes. A tone
can be a frequency or an object with `frequency`, `duration`, `delay`,
`waveform`, `volume`, `attack`, and `release`. `delay` is relative to the call,
so explicitly equal delays make a chord. Waveforms are `sine`, `square`,
`sawtooth`, and `triangle`.

```js
// A short arpeggio, then a two-note chord.
ui.sound.sequence([440, { frequency: 554.37, duration: 140 }, 659.25], {
  duration: 120, waveform: "triangle", volume: 0.08
});
ui.sound.play([{ frequency: 261.63, delay: 420 }, { frequency: 329.63, delay: 420 }], {
  duration: 260, waveform: "sine", volume: 0.07
});
```

`ui.sound.stop()` stops the application's scheduled and active tones. All sound
methods (`playTone`, `play`, `sequence`, and `stop`) return `undefined`; sound
requests are fire-and-forget. Sound is locally generated in the player's
browser and is never uploaded or transmitted. The host unlocks audio on the
first player click, tap, or key press. Before that gesture, calls are scheduled
in the browser's suspended audio context (StationNet has no separate queue) and
start when it is unlocked; do not use a long pre-gesture delay for an important
cue. A call is bounded to 32 tones, with safe frequency, timing, volume, and
voice limits; use timers and short sequences to compose longer music.

### Async values, storage, and downloads

Persistent JSON-compatible values are available asynchronously through
`ui.storage.get`, `set`, `delete`, and `keys`; each returns a `Promise`.
`get(key)` resolves to the stored JSON value, or `null` when the key is missing.
`set` resolves to `true`; `delete` resolves to whether a value was removed; and
`keys` resolves to an array of key strings. Storage is private to the current
user and app by default; pass `{ scope: "shared" }` for community data. Apps
cannot choose another user or application namespace.

`ui.clipboard.writeText(text)`, `ui.download.text(filename, text)`, and
`ui.download.cellImage(filename, rows, options)` each return a `Promise` that
resolves to `true` after the browser accepts the request, or rejects on failure.
`render`, `append`, `update`, `remove`, `clear`, and every `ui.sound.*` method
return `undefined` rather than a promise.

Games can use `ui.scores.submit(score)` and `ui.scores.leaderboard()` for a
station-local leaderboard. Both return promises resolving to an object of the
form:

```js
{
  ok: true,
  personal_best: 42,
  leaderboard: [{ username: "operator", score: 42 }]
}
```

StationNet derives the player identity from the authenticated session, stores
only an improved personal best, and returns at most ten `{ username, score }`
rows. Applications cannot submit for another account.

### Testing and errors

Uncaught runtime exceptions and unhandled rejected async operations are shown to
the player on the application display as `PROGRAM STATUS / ERROR` with an error
message. They are not copied to a sysop console or persisted as an
application-error log. Use the browser developer tools for additional JavaScript
diagnostics; the terminal that runs StationNet can show server-side package/HTTP
errors.

There is no separate package test runner yet. Before installing on a production
station, run a separate development copy of StationNet, upload the ZIP through
**SYSOP CONTROL → MANAGE GAMES AND APPS**, and exercise it there. Upload also
performs the package validation (ZIP shape, manifest, size limits, and UTF-8
script) before it writes or registers anything. The repository test suite can
be run with `python -m pytest`, but it does not execute a third-party package's
gameplay for you.
