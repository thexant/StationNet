import { build } from 'esbuild';

const common = {
  bundle: true,
  minify: true,
  format: 'iife',
  platform: 'browser',
  target: ['chrome100', 'firefox100', 'safari16'],
  legalComments: 'external',
  define: { 'process.env.NODE_ENV': '"production"' },
};

// The editor page: encryption, file list, CodeMirror wiring, and the runner host.
await build({
  ...common,
  entryPoints: ['stationnet/office_basic/main.js'],
  outfile: 'stationnet/static/office-basic.js',
});

// The host for installed BASIC applications on the Games & Apps display page.
await build({
  ...common,
  entryPoints: ['stationnet/office_basic/package_host.js'],
  outfile: 'stationnet/static/application-basic-host.js',
});

// The sandboxed runner. QBJS fonts, GX font sheets, and the bundled QBJS
// libraries are embedded so the opaque-origin frame needs no network access.
await build({
  ...common,
  entryPoints: ['stationnet/office_basic/runner.js'],
  outfile: 'stationnet/static/office-basic-runner.js',
  loader: { '.woff2': 'binary', '.woff': 'binary', '.png': 'binary', '.bas': 'text', '.bi': 'text' },
});
