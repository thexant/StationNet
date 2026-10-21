# StationNet application packages

A bundled application is a directory containing `manifest.json` and `main.js`.
Register its manifest with `_load_package()` in `stationnet/applications.py`.
Application code runs in an isolated worker: it has temporary JavaScript state,
but no StationNet DOM, cookies, database, filesystem, or network access.

The smallest useful application looks like this:

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

## Screens and components

- `render(...components)` replaces the screen; `append(...components)` adds to it.
- Give any component an ID with `{ id: "status" }`, then call
  `update("status", replacement)` or `remove("status")` without rebuilding the
  rest of the screen. `clear()` empties the display.
- `title`, `heading`, and `text` display StationNet-native text.
- `list`, `table`, `preformatted`, and `grid` display structured or fixed-width
  content. A grid is an array of equal-width strings, making ASCII games easy.

```js
ui.render(ui.title("Maze"), ui.grid(map, { id: "map" }));
ui.update("map", ui.grid(nextMap, { id: "map" }));
```

## Input and events

- `button` and `menu` invoke a callback with `{ value, values, key, code }`.
- `input`, `numberInput`, `textarea`, `select`, and `checkbox` add named fields.
  Current field values are supplied in `event.values` to every action callback.
- Text and number inputs accept `onSubmit` in their settings to handle Enter.
  Selects and checkboxes accept `onChange`. Buttons and menu items can be
  disabled.
- `onKey(callback)` receives keyboard events while the application surface has
  focus and returns an unsubscribe function.

```js
const submit = ({ values }) => ui.update(
  "reply", ui.text(`Hello, ${values.name}!`, { id: "reply" })
);
ui.render(
  ui.input("name", "Name", { onSubmit: submit }),
  ui.button("Continue", submit),
  ui.text("", { id: "reply" })
);
```

## Timers and cleanup

`timeout(callback, milliseconds)` and `interval(callback, milliseconds)` each
return a cancel function. StationNet cancels them when the application closes.
Use `onCleanup(callback)` for any other cleanup work.

```js
const stop = ui.interval(() => tick(), 1000);
ui.onCleanup(() => saveTemporaryState());
```

## Persistent storage

`ui.storage` stores JSON-compatible strings, numbers, booleans, arrays, objects,
and `null`. Calls return promises:

```js
await ui.storage.set("settings", { sound: true });
const settings = await ui.storage.get("settings"); // null when missing
const keys = await ui.storage.keys();
await ui.storage.delete("settings");
```

Storage is private to the current user and application by default. Pass
`{ scope: "shared" }` for application-wide community data:

```js
const scores = await ui.storage.get("scores", { scope: "shared" }) || [];
await ui.storage.set("scores", scores, { scope: "shared" });
```

Applications cannot choose a user ID or another application namespace. Each
namespace allows 100 keys; values are limited to 64 KiB. Per-user storage is
limited to 512 KiB per application, and shared storage to 2 MiB per application.
Shared writes are intentionally visible to every user, so they are appropriate
for non-sensitive community state rather than trusted records.

## Lifecycle and errors

Keep ordinary application state in JavaScript closures. `start` may use an async
function, which is useful when loading saved state before the first screen.
Unhandled callbacks and promise errors are reported inside the application
display; they do not replace or interrupt the StationNet shell. Calling
`terminate()` ends the program and runs registered cleanup handlers.

All component content is plain text. HTML, CSS, DOM nodes, and external visual
frameworks are intentionally unsupported.
