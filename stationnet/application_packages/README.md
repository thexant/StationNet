# StationNet application packages

StationNet applications are self-contained ZIP files. The original two-file
package is still valid:

```text
manifest.json
main.js
```

The declared JavaScript entrypoint may be in a subdirectory. Additional modules
and assets can be organized alongside it:

```text
manifest.json
src/main.js
src/message.js
assets/config.json
assets/notes.txt
assets/icon.png
```

Applications run in StationNet's isolated JavaScript worker. Bundled files do
not become web URLs or grant DOM, filesystem, or network access.

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
  "entrypoint": "src/main.js"
}
```

`slug` is the application's permanent, unique identifier. Use lowercase letters,
numbers, and single hyphens; do not change it between releases. `category` must
be `Games` or `Apps`. StationNet currently supports package format `1` and does
not resolve dependencies or perform automatic updates. Existing packages can
keep `"entrypoint": "main.js"` unchanged.

## Create a package

Put the manifest and program files in one folder, then create a ZIP with
`manifest.json` at the *archive root*. On Windows PowerShell, from that folder:

```powershell
Compress-Archive -Path manifest.json,src,assets -DestinationPath my-application.zip
```

Do not ZIP the containing folder itself. See `multifile_demo` for a nested
example, or Number Guess for the original two-file format.

Packages may have at most 64 files, 5 MiB compressed, 4 MiB expanded, and 1 MiB
per file. Paths are case-insensitively unique, at most eight levels deep, and
must use `/` without absolute paths or `..`. Explicit ZIP directory entries are
allowed. Everything needed to run must be inside the ZIP.

### Bundled modules and assets

`ui.assets.bytes(path)` returns a fresh `Uint8Array`; `ui.assets.text(path)`
decodes UTF-8; `ui.assets.json(path)` parses UTF-8 JSON. These methods are
synchronous and throw if the exact package-relative path is missing or invalid.
They do not expose file URLs. Binary assets are available as application data;
the current StationNet UI does not render packaged images or play packaged audio.

`await ui.modules.load(path)` imports a bundled `.js` ES module and returns its
exports. Imports are cached by path. Use this API for each module; ordinary
relative `import` declarations inside a bundled module do not resolve against
the ZIP. Modules run in the same isolated worker as the entrypoint.

```js
StationNet.start(async (ui) => {
  const { greeting } = await ui.modules.load("src/message.js");
  const config = ui.assets.json("assets/config.json");
  ui.render(ui.title(greeting(config.name)));
});
```

```js
// src/message.js
export function greeting(name) { return `Hello, ${name}!`; }
```

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
`checkbox`, `onKey`, and `animate`.

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

`ui.animate(handler)` registers a display-synchronized callback. The callback
receives `{ time, delta }`: `time` is the browser's monotonic animation timestamp
in milliseconds and `delta` is elapsed time in seconds. Its first callback, and
the first callback after a resume or visibility pause, has `delta: 0`. StationNet
waits for a callback (including an async callback) to finish before requesting
another, so slow applications skip frames instead of building a render queue.
Callbacks use browser paint scheduling and are capped near 60 per second;
30–60 FPS is a reasonable target, not a guarantee.

The returned cancellation function follows the timer API and also has
`.pause()`, `.resume()`, and a read-only `.paused` property:

```js
const animation = ui.animate(({ delta }) => {
  const dt = Math.min(delta, 1 / 30); // cap simulation jumps after a slow frame
  updateGame(dt);
  ui.update("field", ui.canvas(640, 400, commands(), { id: "field" }));
});

animation.pause();
animation.resume(); // the next delta is zero
animation();        // permanently cancel
```

Animations pause automatically while the page is hidden or its window is not
focused, and they are cancelled at application termination. Use explicit
pause/resume for game state such as menus. Canvas-only updates to the same id are
coalesced before paint (latest frame wins), and the existing canvas element and
bitmap allocation are reused while its dimensions stay unchanged.

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
{ key, code, phase, repeat, ctrlKey, altKey, shiftKey, metaKey, value, values }
```

`key` and `code` use browser `KeyboardEvent` names, such as `"ArrowLeft"`,
`"a"`, `" "`, and `"KeyA"`; they are not normalized to names such as `"left"`.
`phase` is `"down"` or `"up"`; a held key produces repeated `"down"` events,
marked with `repeat: true`. StationNet sends an `"up"` event for every pressed
key when the application loses focus, so games do not leave movement stuck.
Key handling is skipped while typing in a text input, textarea, or select. The
method returns an unsubscribe function.

`button(label, onClick, options)` remains a click/tap control. Add
`onPress(event)` and/or `onRelease(event)` to `options` to receive the start and
end of a pointer or keyboard press; their `phase` is `"start"` or `"end"`.
The click handler and press handlers may be combined. `ui.menu` accepts the same
`onPress`/`onRelease` options and includes the selected item's string `value`.
Each canvas `controls` entry likewise accepts `{ label, onClick, onPress,
onRelease, variant }`. This allows held touch controls for fast-action games.

`ui.menu(label, items, onSelect, options)` renders a labelled set of buttons.
Each item may be a string (used for both label and value) or
`{ label, value, disabled }`. `onSelect` receives `{ value, values, ... }`,
where `value` is always a string. `options` supports `id`, `onPress`,
`onRelease`, and `{ layout: "dpad" }`; other options are ignored. A d-pad uses the first four
items by *order*, not label: first = up, second = left, third = down, fourth =
right. It does not infer directions from the labels.

### Canvas

`ui.canvas(width, height, commands, options)` creates a raster playfield. Width
and height are logical CSS-pixel units, independently clamped to 80–1,200 and
80–800 respectively (defaults: 640 × 400 when invalid). At most 600 commands
are drawn per call. The background is opaque `#010401`. All drawing is clipped
to the canvas; coordinates outside the visible area are useful for shapes that
cross an edge and cannot paint elsewhere on the page.

The supported command objects are below. Coordinates and dimensions are logical
canvas pixels. Numeric values are finite, clamped to the listed range, and use
the stated default when missing or invalid.

| Command | Fields | Defaults and limits |
| --- | --- | --- |
| `rect` | `type: "rect"`, `x`, `y`, `width`, `height`, `color` | `x`: `0`, −width to 2 × width; `y`: `0`, −height to 2 × height; `width`: `1`, 0 to 2 × width; `height`: `1`, 0 to 2 × height; color defaults to `#00ed45`. |
| `circle` | `type: "circle"`, `x`, `y`, `radius`, `color` | `x`, `y` use the rect coordinate ranges; `radius`: `2`, 0 to the larger canvas dimension; color defaults to `#00ed45`. |
| `text` | `type: "text"`, `x`, `y`, `text`, `size`, `align`, `color` | `x`, `y` use the rect coordinate ranges; text is truncated to 200 characters; `size`: `18`, whole pixels, range 8–72; `align`: `"left"` (or `"center"`/`"right"`); color defaults to `#ffffff`. Text is vertically centered on `y`. |
| `line` | `type: "line"`, `x1`, `y1`, `x2`, `y2`, `color`, `width` | Endpoints must be finite numbers in the range −1,000,000 to 1,000,000. `width` defaults to `1` and is clamped to 0.25–64. Color defaults to `#00ed45`. |
| `lines` | `type: "lines"`, `segments`, `color`, `width` | `segments` is an array of `[x1, y1, x2, y2]` arrays using the line ranges. Invalid segment entries are skipped. All accepted segments are stroked in one batch. |
| `polygon` | `type: "polygon"`, `points`, `fill`, `stroke`, `width` | `points` is an array of at least three `[x, y]` pairs using the line coordinate range. `fill` and `stroke` are independent colors; omit either to disable it. If both are omitted, the polygon is filled with `color` or the default green. `width` controls the stroke as for lines. |

Colors must use 3-, 4-, 6-, or 8-digit hexadecimal CSS notation (for example
`#0f0`, `#0f08`, `#00ed45`, or `#00ed45cc`) or `rgb(r, g, b)` with decimal
integer channels from 0 through 255.
An invalid/missing color becomes the command's default. Unknown command types
are skipped; unknown fields are silently ignored. The host also silently clamps
or normalizes invalid supported values. There is currently no development-mode
warning facility for ignored fields, so validate command construction in your
own tests.

A frame may contain up to 10,000 line segments, 20,000 polygon vertices, and
256 polygons. One polygon may contain up to 4,096 vertices. These geometry
budgets are shared across that canvas draw and are applied in command order.
They are separate from the 600-command limit, so `lines` is the efficient form
for changing wireframes: it creates one canvas path and one stroke for the whole
batch instead of one stroke per segment.

```js
const wireframe = [
  [40, 30, 140, 20], [140, 20, 160, 100], [160, 100, 50, 110],
  [50, 110, 40, 30], [40, 30, 85, 70], [140, 20, 85, 70]
];
ui.update("viewport", ui.canvas(200, 140, [
  { type: "polygon", points: [[-20, 125], [220, 125], [190, 155], [10, 155]], fill: "#062512", stroke: "#19a95b", width: 2 },
  { type: "lines", segments: wireframe, color: "#55ff99", width: 1.5 },
  { type: "line", x1: 0, y1: 125, x2: 200, y2: 125, color: "#ffffff", width: 2 }
], { id: "viewport", label: "Wireframe viewport" }));
```

Canvas options are `{ id, label, onPointer, hover, controls }`. `id` enables
`update`; `label` is the accessible name and defaults to `"Game playfield"`.
`onPointer(event)` receives the scaled logical `{ x, y, phase, values, ... }`;
`phase` is `"start"`, `"move"`, or `"end"`. `hover` defaults to `false`; when
true, mouse moves are reported without a pressed button (touch and dragging are
reported either way). `controls` defaults to no controls and accepts up to four
`{ label, onClick, onPress, onRelease, variant }` entries. `label` defaults to
`"Action"`; `onClick`, `onPress`, and `onRelease` receive the normal interaction
event (the latter two have `phase` `"start"`/`"end"`), and `variant` may be
`"secondary"` (anything else is the default style). Controls are an accessible
centered overlay covering the playfield; there are no placement options.

Here is a complete, short animated canvas example. It draws only the canvas each
frame and uses browser arrow-key names. Visibility pausing is automatic:

```js
StationNet.start((ui) => {
  let x = 130, vx = 0;
  const draw = () => ui.update("field", ui.canvas(320, 180, [
    { type: "text", x: 12, y: 18, text: "ARROWS TO MOVE", size: 12, color: "#55ff55" },
    { type: "circle", x, y: 100, radius: 12, color: "#00ed45" }
  ], { id: "field", label: "Moving dot" }));

  ui.render(ui.title("Dot runner"), ui.canvas(320, 180, [], { id: "field", label: "Moving dot" }));
  ui.onKey(({ key, phase }) => {
    if (phase === "down" && key === "ArrowLeft") vx = -180;
    if (phase === "down" && key === "ArrowRight") vx = 180;
    if (phase === "up" && ["ArrowLeft", "ArrowRight"].includes(key)) vx = 0;
  });
  ui.animate(({ delta }) => {
    x = Math.max(12, Math.min(308, x + vx * delta));
    draw();
  });
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

### Animation performance demo and limits

The built-in Breakout package is the reference sustained-animation demo. It
uses `ui.animate`, time-based movement, and one in-place canvas update per frame;
on capable foreground browsers it is designed to run smoothly in the 30–60 FPS
range. Frame rate remains dependent on the browser, hardware, other page work,
and the complexity of the application's command list.

The remaining hot path is structured-cloning each frame's command data from the
worker and replaying those commands through Canvas 2D. Keep stable page controls
outside the loop, update one canvas id, batch wireframes with `lines`, and avoid
allocating more commands than the scene needs. Large text/geometry command lists
will become CPU and message-copy bound before simple sprite-style scenes do.

## Synthesized sound

Applications can generate sound with basic oscillators. Bundled audio bytes can
be read as data, but StationNet does not play audio files.
`ui.sound.playTone(frequency, duration, options)` plays a
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
