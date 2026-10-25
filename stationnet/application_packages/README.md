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
  "entrypoint": "src/main.js",
  "platforms": ["desktop", "touch"],
  "required_inputs": [],
  "preferred_presentation": "standard"
}
```

`slug` is the application's permanent, unique identifier. Use lowercase letters,
numbers, and single hyphens; do not change it between releases. `category` must
be `Games` or `Apps`. StationNet currently supports package format `1` and does
not resolve dependencies or perform automatic updates. Existing packages can
keep `"entrypoint": "main.js"` unchanged.

The final three fields are optional, so existing packages remain universal and
use the normal StationNet layout. `platforms` may be `"universal"`,
`["desktop"]`, `["touch"]`, or `["desktop", "touch"]`. Input requirements may
contain `keyboard`, `pointer`, `fine-pointer`, `touch`, and `pointer-lock`.
StationNet checks real browser input capabilities first (including hybrid
devices) and uses a desktop/mobile heuristic only where browsers expose no
keyboard capability signal. If a requirement is not met, launch pauses with an
explanation and an explicit **Launch anyway** option.

`preferred_presentation` is `standard` or `immersive`. Immersive presentation
maximizes the app inside the existing StationNet tab; it is independent of
pointer lock and browser fullscreen. Users can always leave it with the visible
**Exit immersive** control or Escape (after Escape has first released pointer
lock, when active).

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
They do not expose file URLs. `ui.assets.image(path)` registers a bundled PNG,
JPEG, GIF, or WebP image for canvas drawing and returns its package-relative
path. Call it once during setup. The trusted renderer decodes and caches the
image asynchronously, and redraws the latest canvas scene when it is ready.
GIFs use their first frame; use sprite sheets for game animation. No package
file becomes a web URL. Bundled PCM WAV files can be played with `ui.sound.load`.

`await ui.assets.decodeImage(path)` decodes a bundled PNG on the trusted host
and returns `{ width, height, pixels }`, with row-major RGBA bytes in a fresh
`Uint8Array`. The optional `palette` field is reserved for indexed decoders;
PNG results currently use RGBA. Decode once during setup and sample the array
in the worker. Decoded images share the image count, byte, dimension, and
8,388,608-pixel budgets with canvas images; repeated calls for one path are
cached and return independent pixel arrays.

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

Bundled applications use the same manifest, runtime, APIs, catalog, and state
controls, and are labeled **BUILT-IN**. Their program files cannot be removed
through the third-party uninstall control. Uploaded apps are labeled
**THIRD-PARTY** and live in the station data directory under `applications/`
rather than StationNet's source tree. Copying the full station data directory
preserves their files, registration, enabled state, and saved data.

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
`heading`, `text`, `list`, `table`, `panel`, `calendar`, `preformatted`, and `grid`. Input methods
include `button`, `menu`, `input`, `numberInput`, `textarea`, `select`,
`dateInput`, `timeInput`, `checkbox`, `onKey`, and `animate`.

`ui.dateInput(name, label, options)` and `ui.timeInput(name, label, options)`
use the browser's native date and time controls. Their values are collected as
`YYYY-MM-DD` and 24-hour `HH:MM` strings in interaction `values`.

`ui.panel(label, components, options)` groups existing components in a titled
section. `ui.calendar(monthLabel, days, onSelect, options)` renders a responsive,
clickable month grid. Supply up to 42 consecutive day objects with `date`
(`YYYY-MM-DD`), `number`, `outside`, `today`, `selected`, up to two `events`
(visible labels), and `extra` (the number of additional events). The selection
callback receives the date as `value`. Calendar labels are rendered as plain
text by the host; applications retain responsibility for event storage and
recurrence rules. Both components accept the usual `id` option.

### Mouse capture and immersive presentation

`await ui.pointerLock.request()` asks StationNet to show a trusted **Capture
mouse** control. The browser request occurs only when the user activates that
control; applications cannot capture the pointer silently. The returned promise
resolves once capture succeeds and rejects if it is cancelled or denied.
`ui.pointerLock.onMove(handler)` supplies relative `{x, y}` movement,
`ui.pointerLock.onChange(handler)` reports capture/release, and
`ui.pointerLock.release()` releases explicitly. Escape, focus loss, navigation,
and application termination also release capture. Pass
`{unadjustedMovement: true}` to request raw movement where the browser supports
it.

`ui.presentation.enterImmersive()` and `exitImmersive()` change only the
StationNet application layout. `ui.presentation.mode` reports `standard` or
`immersive`, and `onChange(handler)` observes changes made by either the app or
the user-facing controls.

A minimal first-person-style controller looks like this:

```js
StationNet.start((ui) => {
  let yaw = 0, pitch = 0;
  const view = () => ui.render(
    ui.title("First-person controls"),
    ui.text(`Yaw ${yaw.toFixed(1)} / Pitch ${pitch.toFixed(1)}`, { id: "look" }),
    ui.button("Capture mouse", async () => {
      try { await ui.pointerLock.request({ unadjustedMovement: true }); }
      catch (error) { ui.append(ui.text(error.message, { tone: "warning" })); }
    })
  );
  ui.pointerLock.onMove(({ x, y }) => {
    yaw += x * 0.12;
    pitch = Math.max(-89, Math.min(89, pitch - y * 0.12));
    ui.update("look", ui.text(`Yaw ${yaw.toFixed(1)} / Pitch ${pitch.toFixed(1)}`, { id: "look" }));
  });
  ui.onKey(({ code, phase }) => {
    if (phase === "down" && code === "KeyI") ui.presentation.enterImmersive();
  });
  view();
});
```

For this example, declare `"platforms": ["desktop"]` and
`"required_inputs": ["keyboard", "fine-pointer", "pointer-lock"]`.

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
active tones and sample voices are stopped. Call unsubscribe functions yourself
when a listener is no longer wanted.

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
are drawn per call, including one `sprites` command for an entire sprite batch.
That batch has a separate 2,000-entry limit per canvas frame. The background is opaque `#010401`. All drawing is clipped
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
| `image` | `type: "image"`, `image`, `x`, `y`, `sx`, `sy`, `sw`, `sh`, `width`, `height`, `flipX`, `flipY`, `opacity` | `image` is a path returned by `ui.assets.image`. The source rectangle defaults to the whole image and destination size defaults to the crop size. Out-of-bounds crops are skipped. Opacity is clamped to 0–1. |
| `sprites` | `type: "sprites"`, `sprites` | One array of up to 2,000 sprite entries across all `sprites` commands in a canvas frame. Each entry has `sheet`, `frame`, `x`, `y`, optional `width`, `height`, `flipX`, `flipY`, `opacity`, `rotation`, and `scale`. Invalid entries and out-of-bounds source frames are skipped. |
| `bitmapText` | `type: "bitmapText"`, `text`, `font`, `x`, `y`, `opacity`, `color` | Draws at most 200 characters per command and 2,000 glyphs per canvas frame. `font` is a `bitmapFont` descriptor. `x`, `y` mark the top left; newlines advance by one character height. `color` applies to built-in fonts. |
| `tilemap` | `type: "tilemap"`, `map`, `x`, `y`, `width`, `height`, `cameraX`, `cameraY`, `opacity` | Draws the visible part of a registered map into a clipped viewport. Camera coordinates are in map pixels and default to zero. |

Colors must use 3-, 4-, 6-, or 8-digit hexadecimal CSS notation (for example
`#0f0`, `#0f08`, `#00ed45`, or `#00ed45cc`) or `rgb(r, g, b)` with decimal
integer channels from 0 through 255.
An invalid/missing color becomes the command's default. Unknown command types
are skipped; unknown fields are silently ignored. The host also silently clamps
or normalizes invalid supported values. There is currently no development-mode
warning facility for ignored fields, so validate command construction in your
own tests.

### Packaged images, sprites, and tile maps

### Procedural framebuffers

`ui.graphics.framebuffer(id, width, height, { format })` creates a persistent
worker-side pixel buffer. `format` is `"rgba"` (default, four bytes per pixel)
or `"indexed"` (one palette index per pixel). Both expose a row-major
`Uint8Array` as `pixels`; indexed buffers also expose a 1,024-byte `palette`
with 256 RGBA entries. The arrays are editable directly for raycasters,
software rasterizers, voxel columns, texture mapping, and palette effects.
`pixel(x, y, value)`, `fillRect(x, y, width, height, value)`, and `clear(value)`
are convenience operations. For RGBA, `value` is `[r, g, b, a]`; alpha defaults
to 255. For indexed color, `value` is a palette index. Direct array loops are
usually faster for whole-frame rendering.

`buffer.blit(src, sx, sy, sw, sh, dx, dy, { colorKey, flipX, flipY })` copies
a clipped source rectangle from another framebuffer or a decoded image.
`buffer.copyFrom(src, dx = 0, dy = 0)` copies the whole source, and
`buffer.scroll(dx, dy, fillValue)` shifts pixels inside the buffer, filling
newly exposed areas. Coordinates are bounded integers. RGBA sources can be
blitted to RGBA buffers; indexed sources can be blitted to indexed or RGBA
buffers using their palette. Indexed destinations require indexed sources.
An indexed `colorKey` is a palette index; an RGBA key is an RGB or RGBA byte
array. Fully transparent RGBA source pixels are skipped. Create an indexed
buffer with `{ format: "indexed", transparentIndex: 0 }` to set palette entry
0's alpha to zero and skip that index when blitting from it. Overlapping
blits within one buffer use a source snapshot.

```js
const wall = await ui.assets.decodeImage("assets/brick.png");
const view = ui.graphics.framebuffer("view", 320, 200);
// In a raycaster's wall-column loop, texX and top/height come from the ray hit.
for (let y = top; y < top + height; y++) {
  const texY = Math.floor((y - top) * wall.height / height);
  const src = (texY * wall.width + texX) * 4;
  view.pixel(column, y, wall.pixels.subarray(src, src + 4));
}
view.present();
```

Call `buffer.present()` after writing pixels, then draw it with
`ui.graphics.frame(buffer, x, y, width, height, { opacity })` inside a canvas.
The frame command can be reused. Present sends a snapshot, so the worker may
edit its own array immediately. For indexed buffers, `present({ paletteOnly:
true })` sends just the palette, reusing the last pixel indices on the host.
This is useful for color cycling or fades. Palette alpha is respected. There
is no readback from the host.

```js
const image = ui.graphics.framebuffer("view", 320, 200, { format: "indexed" });
image.palette.set([255, 160, 32, 255], 4); // palette entry 1
image.fillRect(20, 20, 80, 40, 1);
image.present();
ui.render(ui.canvas(320, 200, [ui.graphics.frame(image, 0, 0, 320, 200)],
  { id: "screen" }));
```

Buffers are limited to four per app, 512,000 pixels combined, and at most
640×400 pixels each. IDs cannot be reused with new dimensions. Each full
`present` transfers 64 KiB/256 KiB for 320×200/640×400 indexed frames, or
256 KiB/1 MiB for RGBA. The relay keeps only the latest frame per buffer for
each display refresh. Indexed palette changes still recolor the cached frame
on the host. Rendering remains Canvas 2D with nearest-neighbor scaling;
there is no WebGL, shader, depth buffer, shared memory, or arbitrary canvas
context. Applications implement depth and texture sampling in their worker.
The built-in **Pixel Raycaster** package demonstrates this at 320×200 and
shows its measured worker render time and delivered frames per second.

`ui.graphics.image(image, x, y, options)` builds an `image` canvas command.
Use `{ sx, sy, sw, sh }` to crop, `{ width, height }` to scale, and `flipX`,
`flipY`, or `opacity` for transparency effects. PNG alpha and other image alpha
are preserved. The renderer uses nearest-neighbor sampling for source scaling;
the canvas also uses pixelated CSS scaling on desktop and mobile.

`ui.graphics.sheet(image, frameWidth, frameHeight, { columns })` describes a
grid of equal-sized frames. `columns` is the number of frames per row (default
1). `ui.graphics.sprite(sheet, frame, x, y, options)` builds an image command
for a zero-based frame. Options are `width`, `height`, `flipX`, `flipY`, and
`opacity`; changing `frame` in `ui.animate` produces animation without reloading
the image. Frame dimensions must be positive integers.

`ui.graphics.sprites(entries)` builds a batched `sprites` command. Each entry
uses a sheet from `ui.graphics.sheet`, a zero-based integer `frame`, and finite
`x`/`y` coordinates. Optional `width`/`height` set the untransformed size;
`flipX`/`flipY` mirror it, `opacity` is 0–1, `rotation` is in radians (clamped
to ±2π), and uniform `scale` is clamped to 0–8. Rotation and scale act about
the sprite center; `x`/`y` are the untransformed top-left corner. Width, height,
and coordinates use the `image` canvas limits. The host inspects at most 2,000
entries in command order per canvas frame, independently of the 600-command
limit. Canvas components in one render may use up to 750 KB of serialized
content; other components retain their 250 KB combined limit.

```js
const atlas = ui.assets.image("assets/actors.png");
const actors = ui.graphics.sheet(atlas, 16, 16, { columns: 8 });
ui.update("game", ui.canvas(320, 200, [ui.graphics.sprites([
  { sheet: actors, frame: 2, x: 20, y: 30 },
  { sheet: actors, frame: 3, x: 70, y: 30, rotation: Math.PI / 4, scale: 2 }
])], { id: "game" }));
```

`ui.graphics.bitmapFont(sheet, { charWidth, charHeight, firstChar })` describes
a grid of fixed-size glyphs in a packaged image. The sheet is filled row by
row; its pixel width and height must be multiples of the character dimensions.
`firstChar` is the Unicode code point of the first glyph (default 32). Character
dimensions must be integers from 1 to 64; `firstChar` is 0–65535. Use
`ui.graphics.fonts.vga8x8` or `ui.graphics.fonts.vga8x16` for the built-in
ASCII 32–126 VGA-style fonts. Unsupported characters leave a blank cell.

`ui.graphics.bitmapText(text, font, x, y, { color, opacity })` builds a
`bitmapText` command. Packaged sheets keep their own colors; `color` tints the
built-in fonts (white by default). `ui.graphics.measureText(text, font)` returns
`{ width, height }` in logical pixels using the same 200-character truncation,
Unicode code-point count, and newline handling as `bitmapText`. Metrics include
blank cells and are exact for the fixed-size grid. The 8×8 font is based on
[font8x8](https://github.com/dhepper/font8x8) (public domain); the 8×16 font
uses [Modern DOS bitmaps from PC Face](https://github.com/susam/pcface)
(CC0).

`ui.graphics.tileMap(id, tiles, { columns, image, tileWidth, tileHeight,
sheetColumns })` registers a rectangular row-major tile array once. Each cell
is a zero-based frame index in the image sheet, or `-1` for an empty cell.
`columns` is the map width in cells; `sheetColumns` is the image width in tile
frames. The method returns `id`. Register again with the same id after editing
the map. `ui.graphics.tileLayer(id, x, y, width, height, { cameraX, cameraY,
opacity })` builds a viewport command; move its camera to scroll. The renderer
draws only visible cells, clips them to the viewport, and caps one layer at
4,096 inspected cells per frame. Maps are cached in the trusted renderer, so
the tile array is transferred only when registered, not on every frame.

```js
StationNet.start((ui) => {
  const atlas = ui.assets.image("assets/atlas.png");
  const hero = ui.graphics.sheet(atlas, 16, 16, { columns: 8 });
  const map = ui.graphics.tileMap("level", [0, 1, 2, 3, 3, 2, 1, 0], {
    columns: 4, image: atlas, tileWidth: 16, tileHeight: 16, sheetColumns: 8
  });
  ui.render(ui.canvas(320, 180, [], { id: "game", label: "Game scene" }));
  let time = 0;
  ui.animate(({ delta }) => {
    time += delta;
    ui.update("game", ui.canvas(320, 180, [
      ui.graphics.tileLayer(map, 0, 0, 320, 180, { cameraX: time * 8 }),
      ui.graphics.sprite(hero, Math.floor(time * 8) % 4, 100, 80,
        { width: 32, height: 32, flipX: true })
    ], { id: "game", label: "Game scene" }));
  });
});
```

Up to 32 images (at most 2,048 × 2,048 pixels each, with an 8-million-pixel
combined decoded budget) and 16 maps (at most
262,144 cells each) may be registered per app. Existing package limits remain:
pixel art PNG sheets typically fit within the 1 MiB per-file limit. Image
decode may briefly leave a newly loaded sprite blank; the renderer redraws it
once decoding finishes. Keep source images and map cell sizes modest on mobile.

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

Command-heavy scenes still pay for cloning and replaying every command. Images,
tile maps, and procedural framebuffers are cached by the host. The relay
coalesces repeated canvas and framebuffer updates before each display refresh
and attaches pixel data to the canvas update, keeping the normal animated path
to one host message per frame. Keep stable page controls outside the loop,
update one canvas id, and batch wireframes with `lines`. For dense procedural
scenes, write directly into an indexed framebuffer instead of emitting many
rectangles or columns as commands.

`node tests/benchmark_application_framebuffer.js` is a reproducible CPU-only
microbenchmark (Node, 300 timed iterations after warm-up). On the development
machine it measured **0.14 ms** for a 320×200 textured-column fill plus
snapshot and **0.18 ms** for palette expansion; at 640×400 the respective
times were **0.51 ms** and **0.74 ms**. These numbers exclude browser message
delivery, Canvas 2D upload, scaling, and display synchronization. The Pixel
Raycaster app reports its own live worker timing and delivered frame rate so
browser and device performance can be checked directly.

## Sound

Applications can generate sound with basic oscillators and play bundled PCM WAV
samples. Other bundled audio formats can be read as bytes but are not played.
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

`await ui.sound.load(path)` decodes a bundled `.wav` file on the trusted host and
returns a sample handle. WAV files must contain uncompressed PCM, 8 or 16 bit,
mono or stereo, at 8,000–48,000 Hz. `ui.sound.createSample(samples, sampleRate)`
returns a handle for a mono `Int8Array`, `Int16Array`, or `Float32Array`. Integer
arrays are signed PCM; floats must be finite and between -1 and 1. The sample
rate has the same range as WAV. Each app can hold 32 samples, each at most 2 MiB
after decoding (four bytes per channel sample). Package files remain subject to
the 1 MiB file limit.

`ui.sound.playSample(handle, { volume, pan, loop, rate })` returns a voice ID.
Volume is 0–1 (default 1), pan is -1 to 1 (default 0), loop is a boolean
(default false), and rate is 0.25–4 (default 1). Up to eight samples can play at
once; starting another steals the oldest voice. `ui.sound.stopSample(voiceId)`
stops one voice. `ui.sound.stop()` stops all active tones and sample voices.

```js
const bell = await ui.sound.load("assets/bell.wav");
const voice = ui.sound.playSample(bell, { volume: 0.6, pan: -0.3 });
ui.sound.stopSample(voice);
const wave = Int8Array.from([0, 64, 127, 64, 0, -64, -128, -64]);
const custom = ui.sound.createSample(wave, 8000);
ui.sound.playSample(custom, { loop: true, rate: 0.5 });
```

`playTone`, `play`, `sequence`, `stop`, and `stopSample` return `undefined`.
`createSample`, `playSample`, and `stopSample` act synchronously; `load` returns a
promise. Sound is locally generated in the player's browser and is never
uploaded or transmitted. The host unlocks audio on the
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
Each JSON value can occupy up to 2 MiB. An app may use up to 16 MiB per user
and 2 MiB of shared storage, across at most 100 keys in each namespace.

`ui.clipboard.writeText(text)`, `ui.download.text(filename, text)`, and
`ui.download.cellImage(filename, rows, options)` each return a `Promise` that
resolves to `true` after the browser accepts the request, or rejects on failure.
`render`, `append`, `update`, `remove`, and `clear` return `undefined` rather
than a promise. Sound method return values are described above.

### Binary files and General MIDI playback

`await ui.files.open({ accept: ".mid,.midi,audio/midi" })` displays a trusted
**Choose file** control. It resolves to `{ name, bytes }`, where `bytes` is an
array of byte values. The user chooses the file explicitly. File size is limited
to 2 MiB. Cancelling rejects the promise. `await ui.download.binary(filename,
bytes, mime)` downloads up to 2 MiB; the host checks every byte, sanitizes the
filename, and creates the Blob outside the isolated worker.

`await ui.display.viewport()` returns the available display width and browser
height in CSS pixels. Use it when a canvas needs a compact touch layout.

The bundled SoundFont renderer is available through `ui.midi`. Pass Standard
MIDI File bytes to `await ui.midi.load(bytes)`, then call `play()`, `pause()`,
`stop()`, `seek(seconds)`, or `position()` (seconds). Loading a new file replaces
the previous sequence. Playback uses the same General MIDI engine and SoundFont
as StationNet Radio; the host stops the sequence when the app closes. Browser
audio must be unlocked by a user gesture, so start playback from a button or
pointer action. The methods return promises and report loading or synthesis
errors to the app. For example:

```js
StationNet.start(async (ui) => {
  ui.render(ui.button("Open and play MIDI", async () => {
    const file = await ui.files.open({ accept: ".mid,.midi" });
    await ui.midi.load(file.bytes);
    await ui.midi.play();
  }));
});
```

`midi-composer` is a bundled example using these APIs, private project storage,
and a pointer driven piano roll. Its MIDI parser and writer are in `midi.js`.

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

## Multiplayer

Multiplayer is station-local and opt-in. Add `"permissions": ["multiplayer"]`
to the manifest. Packages without this declaration retain their prior API and
behavior. Identity always comes from the signed-in StationNet session;
applications receive only an opaque `player:N` id, username, connection status,
and host status. They cannot supply a user id.

The API is available at `ui.multiplayer`:

| Method | Result / behavior |
| --- | --- |
| `create({ name, maxPlayers, visibility, state })` | Creates and joins a lobby. Limits are 2–64; visibility is `public` (default) or `private`. Private results include `session.join_code`. |
| `discover()` | Returns `{ sessions }` for this app's public open lobbies. |
| `join(sessionId, { joinCode })` | Joins or reconnects the authenticated account. Private sessions require their code. |
| `leave()` | Leaves explicitly. Host departure transfers ownership to the longest-standing remaining player. |
| `send(event, data)` | Broadcasts a JSON event from the authenticated player. Data is limited to 32 KiB. |
| `snapshot()` | Returns session metadata, permission-safe players, authoritative state/revision, and recent events. |
| `setState(state, { expectedRevision })` | Host-only replacement of authoritative shared state (256 KiB maximum). The optional revision provides optimistic concurrency control. |
| `onEvent(handler)` | Subscribes to 500 ms polling updates and returns an unsubscribe function. The handler receives `(event, snapshot)`. |
| `connectRealtime({ maxRate })` | Opens a station-local WebSocket for the current room. `maxRate` is an integer from 1 to 60 events per second (default 30). Resolves with `{ connected: true, maxRate }`. |
| `sendRealtime(event, data)` | Queues a transient JSON event for other connected players in this room. Resolves with `{ queued: true }`; it does not acknowledge delivery to each player. |
| `onRealtimeEvent(handler)` | Subscribes to transient `{ type: "event", event, data, player }` messages and connection updates `{ type: "presence", player_id, connected }`. Returns an unsubscribe function. A host change is `{ type: "host", player_id }`. |
| `disconnectRealtime()` | Closes this runtime's WebSocket. Lobby membership remains until `leave()` or reconnect expiry. |
| `createSync(options)` | Creates an optional fixed-rate host simulation helper with input validation, snapshot interpolation, and reconnect handling. Call `await sync.start(playerId)` after joining. |

State is persisted in StationNet's SQLite database. Event history is bounded to
500 entries per lobby and each poll returns at most 100. The trusted host relays
requests; sandboxed application code has no direct network or authentication
access. A closed page is marked disconnected and the same account can reconnect.
Missing heartbeats are inferred after 30 seconds, membership is retained for a
five-minute reconnect grace period, empty sessions close, and old closed
sessions are removed.

Use real-time events for movement, inputs, cursor positions, and other replaceable
updates. They are delivered only to connected members of the same room, are not
written to SQLite, and are not included in `snapshot()` or `onEvent()`. Open the
socket after `create()` or `join()`. A disconnected client should call
`connectRealtime()` again and use `snapshot()` to restore durable state. The
existing `send()`, `onEvent()`, `setState()`, discovery, private join codes,
revisions, and host migration continue to work unchanged. The browser host owns
the socket; sandboxed application code receives only the StationNet API and has
no direct network access.

On a local station network, WebSocket events normally arrive within one network
round trip plus server scheduling, without the 500 ms polling interval. This is
an expected behavior, not a latency guarantee; Wi-Fi, device load, and rendering
can add delay. Use a fixed game tick or interpolation if the game needs smooth
motion. The server allows one active socket per player and room, 512 total
connections, 16 KiB frames, a 64-message outgoing queue per socket, and at most
the configured 1-60 inbound updates per second with a one-second burst. It
closes sockets that exceed limits or send invalid messages. Event payloads must
be JSON. Real-time delivery is best effort: keep critical game results in the
existing durable state or event API. The in-memory relay requires one StationNet
server process; the supplied desktop and Android launchers use one process.

### Synchronized action games

`createSync` keeps game rules in your application. The current host receives
authenticated, ordered input packets and advances the simulation at `tickRate`
(20 Hz by default). Return compact JSON state from `onTick` to publish it.
Other players render `sync.sample()`, which interpolates numeric fields between
the two latest snapshots with a 100 ms delay by default. Input validation and
collision rules belong in the host callback.

```js
// Both signed-in players run this after create() or join().
const joined = await ui.multiplayer.join(roomId); // host uses create() instead
const sync = ui.multiplayer.createSync({
  tickRate: 20,
  validateInput(input) {
    return input && Number.isInteger(input.dx) && Math.abs(input.dx) <= 1;
  },
  onTick({ state, inputs, dt, isHost }) {
    if (!isHost) return;
    const next = { players: { ...(state?.players || {}) } };
    for (const packet of inputs) {
      const id = packet.player.id; // authenticated by StationNet
      const x = next.players[id]?.x || 0;
      next.players[id] = { x: Math.max(0, Math.min(100, x + packet.data.dx * 4 * dt)) };
    }
    return next;
  },
  onState() { draw(sync.sample()); },
  onHost(playerId) { /* The new host resumes simulation. */ },
  onReconnect() { draw(sync.sample()); }
});
await sync.start(joined.player_id);
await sync.input({ dx: 1 });
// Call sync.stop() when leaving the game.
```

`sync.input(data)` sends increasing input ids; the relay stamps server time and
room sequence and forwards inputs only to the host. The host may reject an input
in `validateInput`, then publishes state with a monotonic tick. The relay rejects
state from nonhosts and old host epochs. `sync.state` is the latest authoritative
state. `sync.sample()` is for presentation. Optional `predict(state, input)`
shows provisional local motion; the next host snapshot replaces it. Apps that
need full replay reconciliation can maintain their own input history.

The helper reconnects its socket and restores the latest in-memory state from
the relay. `sync.resync()` requests that state explicitly. If the host departs,
authority transfers to a connected member and the relay announces a new epoch
with its last accepted state. The new host resumes from that state and starts
ticks at zero. Cached state is kept for up to five minutes after all sockets
disconnect. Use `setState` for durable checkpoints at game milestones. Movement
snapshots do not write to SQLite. After a server restart, restore from
`snapshot()` or initialize a fresh round. StationNet checks membership and host
identity; each app defines its own game rules.

```js
await ui.multiplayer.join(roomId, { joinCode });
await ui.multiplayer.connectRealtime({ maxRate: 30 });
const unsubscribe = ui.multiplayer.onRealtimeEvent((message) => {
  if (message.type === "event" && message.event === "position") {
    // Draw or interpolate message.data for message.player.id.
  }
  if (message.type === "presence") {
    // message.player_id is connected or disconnected.
  }
});
await ui.multiplayer.sendRealtime("position", { x: 10, y: 20 });
```

Event objects are `{ id, event, data, player, created_at }`. For player-sent
events, `player` contains only `{ id, username }`. StationNet also emits the
reserved lifecycle events `player_joined`, `player_left`, `player_disconnected`,
`player_reconnected`, `host_changed`, and `state_changed`; applications cannot
spoof those names. A snapshot's player entries add `connected` and `is_host`.

Only the host can replace shared state. Use the latest snapshot revision as
`expectedRevision`; stale writes are rejected. Game-specific rules remain
application-defined, so authoritative games should have the host validate
received actions and publish accepted state instead of treating events as state.

```js
StationNet.start(async (ui) => {
  const created = await ui.multiplayer.create({
    name: "My lobby", maxPlayers: 2, state: { turn: 0 }
  });
  ui.multiplayer.onEvent(async (event, view) => {
    if (created.is_host && event.event === "move") {
      await ui.multiplayer.setState(
        { turn: view.state.turn + 1, lastMove: event.data },
        { expectedRevision: view.revision }
      );
    }
  });
  // A second authenticated user can call:
  // await ui.multiplayer.join(created.session.id)
  await ui.multiplayer.send("move", { x: 1, y: 2 });
});
```

The built-in **Signal Pairs** package demonstrates two authenticated users: one
creates a two-seat session, another joins its displayed id, and both exchange
events.
