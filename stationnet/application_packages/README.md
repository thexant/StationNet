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

## Visual API

- `render(...components)` replaces the screen; `append(...components)` adds to it.
- `title`, `heading`, and `text` display formatted StationNet-native text.
- `button` and `menu` invoke a callback with `{ value, values, key, code }`.
- `input` and `numberInput` add named fields. Their current values are supplied
  to action callbacks in `event.values`.
- `list`, `table`, `preformatted`, and `grid` display structured content.
- `onKey(callback)` receives keyboard events while the application surface has
  focus and returns a function that removes the listener.
- `onCleanup(callback)` registers cleanup work for normal termination.

All component content is plain text. HTML, CSS, DOM nodes, and external visual
frameworks are intentionally unsupported.
