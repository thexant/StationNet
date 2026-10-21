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
`checkbox`, and `onKey`. Timers use `timeout` and `interval`; register cleanup
with `onCleanup`.

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

Persistent JSON-compatible values are available asynchronously through
`ui.storage.get`, `set`, `delete`, and `keys`. Storage is private to the current
user and app by default; pass `{ scope: "shared" }` for community data. Apps
cannot choose another user or application namespace.
